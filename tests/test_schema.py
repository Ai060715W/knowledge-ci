import unittest

from src.registry.schema import (
    RegistryValidationError,
    STATUS_TRANSITIONS,
    UNIT_STATUSES,
    is_injectable,
    migrate_registry,
    migrate_unit,
    transition_status,
    unit_name,
    unit_patterns,
    unit_symbols,
    unit_title,
    validate_registry,
    validate_unit,
)


def make_v2_unit(**overrides):
    unit = {
        "id": "payment_retry",
        "title": "支付重试",
        "summary": "重试上限 3 次",
        "rationale": "幂等性保证",
        "scope": {"files": ["src/payment/retry.py"], "symbols": ["PaymentRetry"]},
        "evidence": [{"type": "commit", "id": "a13f9c2"}],
        "confidence": 0.6,
        "owner": "payment-team",
        "reviewer": None,
        "status": "active",
        "risk_level": "HIGH",
        "knowledge_delta": {"ops": [{"insert": "text"}]},
        "related_docs": [],
        "last_verified": "2026-08-17",
        "code_hash": "",
        "version": 1,
    }
    unit.update(overrides)
    return unit


class SchemaValidationTest(unittest.TestCase):
    def test_valid_v2_unit_passes(self):
        self.assertEqual(validate_unit(make_v2_unit()), [])

    def test_missing_required_fields_reported(self):
        errors = validate_unit({"id": "x"})
        messages = "\n".join(errors)
        self.assertIn("title", messages)
        self.assertIn("status", messages)
        self.assertIn("version", messages)

    def test_invalid_status_rejected(self):
        errors = validate_unit(make_v2_unit(status="draft"))
        self.assertTrue(any("'draft'" in error for error in errors))

    def test_confidence_out_of_range_rejected(self):
        errors = validate_unit(make_v2_unit(confidence=1.5))
        self.assertTrue(any("1.5" in error for error in errors))

    def test_evidence_type_enum_enforced(self):
        errors = validate_unit(make_v2_unit(evidence=[{"type": "blog"}]))
        self.assertTrue(errors)

    def test_unknown_unit_fields_are_preserved(self):
        unit = make_v2_unit(extra_meta={"custom": True})
        self.assertEqual(validate_unit(unit), [])

    def test_valid_v2_registry_passes(self):
        registry = {"version": 2, "last_updated": "2026-08-17", "units": [make_v2_unit()]}
        self.assertEqual(validate_registry(registry), [])

    def test_legacy_v1_registry_gets_structural_check_only(self):
        registry = {
            "version": 1,
            "units": [{"id": "payment_retry", "file_pattern": "src/payment/retry.py"}],
        }
        self.assertEqual(validate_registry(registry), [])

    def test_broken_v1_registry_reports_structure(self):
        registry = {"version": 1, "units": [{"file_pattern": "no-id"}]}
        errors = validate_registry(registry)
        self.assertTrue(any("id" in error for error in errors))


class StatusMachineTest(unittest.TestCase):
    def test_all_statuses_known(self):
        self.assertEqual(len(UNIT_STATUSES), 5)

    def test_legal_transition_applies(self):
        unit = make_v2_unit(status="proposed")
        self.assertEqual(transition_status(unit, "under_review")["status"], "under_review")

    def test_illegal_transition_raises(self):
        unit = make_v2_unit(status="active")
        with self.assertRaises(RegistryValidationError):
            transition_status(unit, "proposed")

    def test_retired_is_terminal(self):
        unit = make_v2_unit(status="retired")
        for target in UNIT_STATUSES:
            with self.subTest(target=target):
                if target != "retired":
                    with self.assertRaises(RegistryValidationError):
                        transition_status(unit, target)

    def test_unknown_status_raises(self):
        with self.assertRaises(RegistryValidationError):
            transition_status(make_v2_unit(), "draft")

    def test_legacy_unit_without_status_counts_as_active(self):
        unit = make_v2_unit()
        del unit["status"]
        self.assertEqual(transition_status(unit, "outdated")["status"], "outdated")
        self.assertEqual(is_injectable(make_v2_unit(status="proposed")), False)
        self.assertEqual(is_injectable(make_v2_unit(status="active")), True)

    def test_transition_map_coverage(self):
        self.assertEqual(set(STATUS_TRANSITIONS), set(UNIT_STATUSES))


class MigrationTest(unittest.TestCase):
    def test_v1_unit_fields_map_to_v2(self):
        v1 = {
            "id": "payment_retry",
            "name": "支付重试",
            "file_pattern": "src/payment/retry.py",
            "risk_level": "HIGH",
            "knowledge_delta": {"ops": [{"insert": "x"}]},
            "related_docs": ["docs/p.md"],
            "last_verified": "2026-08-01",
            "code_hash": "abc",
            "version": 3,
        }
        migrated = migrate_unit(v1)
        self.assertEqual(migrated["title"], "支付重试")
        self.assertEqual(migrated["scope"]["files"], ["src/payment/retry.py"])
        self.assertEqual(migrated["scope"]["symbols"], [])
        self.assertEqual(migrated["status"], "active")
        self.assertEqual(migrated["confidence"], None)
        self.assertEqual(migrated["evidence"], [])
        self.assertEqual(migrated["version"], 3)
        self.assertNotIn("file_pattern", migrated)
        self.assertNotIn("name", migrated)
        self.assertEqual(validate_unit(migrated), [])

    def test_existing_scope_files_keep_legacy_pattern_first(self):
        v1 = {
            "id": "u",
            "file_pattern": "src/a.py",
            "scope": {"files": ["src/b.py"], "symbols": ["B"]},
        }
        migrated = migrate_unit(v1)
        self.assertEqual(migrated["scope"]["files"], ["src/a.py", "src/b.py"])
        self.assertEqual(migrated["scope"]["symbols"], ["B"])

    def test_v1_unit_without_name_uses_id_as_title(self):
        migrated = migrate_unit({"id": "solo"})
        self.assertEqual(migrated["title"], "solo")

    def test_migrate_registry_is_idempotent(self):
        v1 = {"version": 1, "units": [{"id": "u", "name": "U"}]}
        migrated, warnings = migrate_registry(v1)
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(warnings, [])
        again, _ = migrate_registry(migrated)
        self.assertEqual(again["version"], 2)
        self.assertEqual(again["units"][0]["title"], "U")

    def test_migrate_registry_validates_after_migration(self):
        migrated, _ = migrate_registry({"version": 1, "units": [{"id": "u"}]})
        self.assertEqual(validate_registry(migrated), [])


class UnitAccessorTest(unittest.TestCase):
    def test_patterns_prefer_scope_files_with_legacy_fallback(self):
        self.assertEqual(unit_patterns(make_v2_unit()), ["src/payment/retry.py"])
        self.assertEqual(unit_patterns({"file_pattern": "src/*.py"}), ["src/*.py"])
        self.assertEqual(unit_patterns({"scope": {"files": ["a.py", "a.py"]}}), ["a.py"])

    def test_symbols_read_from_scope(self):
        self.assertEqual(unit_symbols(make_v2_unit()), ["PaymentRetry"])
        self.assertEqual(unit_symbols({"file_pattern": "x"}), [])

    def test_title_and_name_fallbacks(self):
        self.assertEqual(unit_title(make_v2_unit()), "支付重试")
        self.assertEqual(unit_title({"name": "旧名", "id": "u"}), "旧名")
        self.assertEqual(unit_title({"id": "u"}), "u")
        self.assertEqual(unit_name({"title": "T"}), "T")


if __name__ == "__main__":
    unittest.main()
