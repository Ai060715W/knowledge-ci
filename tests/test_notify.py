import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from src.cli.notify import run as notify_run
from src.metrics.notify import (
    UNASSIGNED_OWNER,
    build_notification_report,
    write_notification_report,
)


def write_registry(path: Path, units: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 2, "last_updated": "", "units": units}, ensure_ascii=False),
        encoding="utf-8",
    )


def make_unit(unit_id: str, owner: str | None, status: str = "active") -> dict:
    return {
        "id": unit_id,
        "title": unit_id,
        "status": status,
        "owner": owner,
        "version": 1,
        "scope": {"files": [f"src/{unit_id}.py"], "symbols": []},
        "knowledge_delta": {"ops": [{"insert": "k"}]},
        "evidence": [],
    }


def write_patch(patches_dir: Path, patch_id: str, unit_id: str, status: str, **extra) -> None:
    patches_dir.mkdir(parents=True, exist_ok=True)
    patch = {
        "patch_id": patch_id,
        "status": status,
        "unit_id": unit_id,
        "generated_at": extra.pop("generated_at", "2026-09-01T00:00:00Z"),
        **extra,
    }
    (patches_dir / f"patch_{patch_id}.json").write_text(
        json.dumps(patch, ensure_ascii=False), encoding="utf-8"
    )


class NotifyReportTest(unittest.TestCase):
    def test_groups_patches_and_units_by_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            patches = root / "patches"
            write_registry(
                registry_path,
                [
                    make_unit("u1", "payment-team"),
                    make_unit("u2", "payment-team", status="under_review"),
                    make_unit("u3", "platform", status="outdated"),
                ],
            )
            write_patch(patches, "kp_1", "u1", "APPLIED", new_version=2)
            write_patch(patches, "kp_2", "u1", "PENDING", risk_level="HIGH")
            write_patch(patches, "kp_3", "u3", "REJECTED", status_reason="needs work")

            report = build_notification_report(registry_path, patches)

        self.assertEqual(set(report["owners"]), {"payment-team", "platform"})
        payment = report["owners"]["payment-team"]
        self.assertEqual([item["patch_id"] for item in payment["pending_review"]], ["kp_2"])
        self.assertEqual([item["patch_id"] for item in payment["changed"]], ["kp_1"])
        self.assertEqual(payment["changed"][0]["new_version"], 2)
        self.assertEqual(payment["rejected"], [])
        self.assertEqual(payment["units"], {"u1": "active", "u2": "under_review"})
        self.assertEqual(payment["units_under_review"], ["u2"])
        platform = report["owners"]["platform"]
        self.assertEqual([item["patch_id"] for item in platform["rejected"]], ["kp_3"])
        self.assertEqual(platform["rejected"][0]["status_reason"], "needs work")
        self.assertEqual(platform["changed"], [])  # REJECTED never counts as changed
        self.assertEqual(platform["units_outdated"], ["u3"])
        summary = report["summary"]
        self.assertEqual(summary["owners"], 2)
        self.assertEqual(summary["pending_review"], 1)
        self.assertEqual(summary["changed"], 1)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(summary["units_under_review"], 1)
        self.assertEqual(summary["units_outdated"], 1)
        self.assertEqual(summary["status_distribution"], {"active": 1, "under_review": 1, "outdated": 1})
        self.assertEqual(summary["warnings"], 0)

    def test_null_owner_and_unknown_unit_fall_into_unassigned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            patches = root / "patches"
            write_registry(registry_path, [make_unit("u1", None)])
            write_patch(patches, "kp_1", "u1", "PENDING", generated_at="2026-08-01T00:00:00Z")
            write_patch(patches, "kp_2", "ghost_unit", "PENDING", generated_at="2026-09-01T00:00:00Z")

            report = build_notification_report(registry_path, patches)

        unassigned = report["owners"][UNASSIGNED_OWNER]
        patch_ids = [item["patch_id"] for item in unassigned["pending_review"]]
        self.assertEqual(patch_ids, ["kp_2", "kp_1"])  # newest generated_at first
        unit_ids = {item["unit_id"] for item in unassigned["pending_review"]}
        self.assertEqual(unit_ids, {"u1", "ghost_unit"})  # unresolved unit_id kept for triage
        self.assertEqual(report["summary"]["owners"], 1)

    def test_newest_patch_first_within_bucket(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            patches = root / "patches"
            write_registry(registry_path, [make_unit("u1", "team")])
            write_patch(patches, "kp_old", "u1", "APPLIED", generated_at="2026-01-01T00:00:00Z")
            write_patch(patches, "kp_new", "u1", "APPLIED", generated_at="2026-09-01T00:00:00Z")

            report = build_notification_report(registry_path, patches)

        changed = report["owners"]["team"]["changed"]
        self.assertEqual([item["patch_id"] for item in changed], ["kp_new", "kp_old"])

    def test_corrupt_and_malformed_patches_become_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            patches = root / "patches"
            write_registry(registry_path, [make_unit("u1", "team")])
            patches.mkdir()
            (patches / "patch_broken.json").write_text("{not json", encoding="utf-8")
            (patches / "patch_nounit.json").write_text(json.dumps({"status": "PENDING"}), encoding="utf-8")
            (patches / "patch_badstatus.json").write_text(
                json.dumps({"status": "WEIRD", "unit_id": "u1"}), encoding="utf-8"
            )
            write_patch(patches, "kp_ok", "u1", "PENDING")

            report = build_notification_report(registry_path, patches)

        self.assertEqual(len(report["warnings"]), 3)
        self.assertEqual(report["summary"]["warnings"], 3)
        self.assertEqual(report["summary"]["pending_review"], 1)
        self.assertEqual(
            [item["patch_id"] for item in report["owners"]["team"]["pending_review"]], ["kp_ok"]
        )

    def test_empty_or_missing_patches_dir_yields_empty_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            write_registry(registry_path, [make_unit("u1", "team")])

            report_empty = build_notification_report(registry_path, root / "patches")
            report_missing = build_notification_report(registry_path, root / "nope")
            report_none = build_notification_report(registry_path, None)

        for report in (report_empty, report_missing, report_none):
            self.assertEqual(report["summary"]["pending_review"], 0)
            self.assertEqual(report["summary"]["changed"], 0)
            self.assertEqual(report["owners"]["team"]["units"], {"u1": "active"})

    def test_missing_registry_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(FileNotFoundError) as ctx:
                build_notification_report(root / "nope.json", root)
        self.assertIn("Registry not found", str(ctx.exception))

    def test_report_carries_delivery_extension_point(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            write_registry(registry_path, [make_unit("u1", "team")])
            report = build_notification_report(registry_path, root / "patches")
        self.assertEqual(report["delivery"]["channels"], ["local_file"])
        self.assertIn("build_notification_report()", report["delivery"]["note"])
        self.assertTrue(report["generated_at"].endswith("Z"))

    def test_write_notification_report_names_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            write_registry(registry_path, [make_unit("u1", "team")])
            report = build_notification_report(registry_path, root / "patches")
            output = write_notification_report(report, root / "reports", timestamp="20260916_120000")
            loaded = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(output.name, "notify_20260916_120000.json")
        self.assertEqual(loaded["summary"]["owners"], 1)


class NotifyCliTest(unittest.TestCase):
    def test_cli_end_to_end_with_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            knowledge_dir = root / ".knowledge-ci"
            (knowledge_dir / "data" / "patches").mkdir(parents=True)
            registry_path = knowledge_dir / "data" / "registry.json"
            write_registry(registry_path, [make_unit("u1", "team")])
            write_patch(knowledge_dir / "data" / "patches", "kp_1", "u1", "PENDING")
            config = knowledge_dir / "config.yaml"
            config.write_text(
                "\n".join(
                    [
                        'project_path: ".."',
                        'registry_path: "data/registry.json"',
                        'reports_path: "data/reports"',
                        'patches_path: "data/patches"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            exit_code = notify_run(Namespace(config=str(config), registry=None, patches=None, out=None))
            outputs = list((knowledge_dir / "data" / "reports").glob("notify_*.json"))
            loaded = json.loads(outputs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(loaded["summary"]["pending_review"], 1)

    def test_cli_explicit_paths_without_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            write_registry(registry_path, [make_unit("u1", "team")])
            patches = root / "patches"
            write_patch(patches, "kp_1", "u1", "APPLIED")
            out = root / "out"

            exit_code = notify_run(
                Namespace(config=None, registry=str(registry_path), patches=str(patches), out=str(out))
            )
            outputs = list(out.glob("notify_*.json"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(outputs), 1)

    def test_cli_missing_registry_exits_with_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(SystemExit):
                notify_run(Namespace(config=None, registry=None, patches=None, out=str(root)))


if __name__ == "__main__":
    unittest.main()
