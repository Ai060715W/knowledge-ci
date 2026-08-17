import json
import tempfile
import unittest
from pathlib import Path

from src.patch.delta import DeltaValidationError, apply_delta_ops, validate_delta_ops
from src.patch.generator import build_patch, parse_model_delta
from src.patch.pr_manager import apply_patch_to_registry, build_pr_body, create_pr, discover_codeowners, mark_patch_status
from src.patch.prompts import build_prompt


class PatchPhaseTest(unittest.TestCase):
    def test_delta_validation_and_application(self):
        ops = validate_delta_ops([{"retain": 5}, {"delete": 1}, {"insert": "3"}])
        self.assertEqual(apply_delta_ops("retry5", ops), "retry3")

    def test_delta_rejects_invalid_operation_shape(self):
        with self.assertRaises(DeltaValidationError):
            validate_delta_ops([{"retain": 1, "insert": "x"}])

    def test_delta_rejects_out_of_bounds_retain(self):
        with self.assertRaises(DeltaValidationError):
            apply_delta_ops("abc", [{"retain": 4}])

    def test_model_response_must_be_raw_json_array(self):
        with self.assertRaises(ValueError):
            parse_model_delta("```json\n[]\n```")

    def test_prompt_contains_high_risk_constraints_and_few_shot(self):
        prompt = build_prompt(
            {"id": "payment_retry", "risk_level": "HIGH", "knowledge_delta": {"ops": [{"insert": "old"}]}},
            "MAX_RETRY changed",
            ["src/payment/retry.py"],
        )
        self.assertIn("资深架构师", prompt)
        self.assertIn('"retain"', prompt)
        self.assertIn("禁止使用", prompt)

    def test_build_patch_writes_pending_patch_from_mock_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            reports_path = root / "reports"
            patches_path = root / "patches"
            reports_path.mkdir()
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "units": [
                            {
                                "id": "payment_retry",
                                "risk_level": "HIGH",
                                "version": 1,
                                "knowledge_delta": {"ops": [{"insert": "支付重试上限为 5 次。"}]},
                                "related_docs": ["docs/payment.md"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (reports_path / "impact_abc1234.json").write_text(
                json.dumps(
                    {
                        "commit": "abc123456789",
                        "commit_short": "abc1234",
                        "changed_files": [
                            {
                                "path": "src/payment/retry.py",
                                "status": "modified",
                                "unit_id": "payment_retry",
                                "summary": {
                                    "functions": ["retry_payment"],
                                    "classes": [],
                                    "constants": ["MAX_RETRY"],
                                    "diff_excerpt": ["-MAX_RETRY = 5", "+MAX_RETRY = 3"],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            patch, output_path = build_patch(
                commit="abc1234",
                unit_id="payment_retry",
                registry_path=registry_path,
                reports_path=reports_path,
                patches_path=patches_path,
                model="gpt-4o-mini",
                mock_response='[{"retain": 8}, {"delete": 1}, {"insert": "3"}]',
            )

        self.assertTrue(output_path.name.startswith("patch_kp_"))
        self.assertEqual(patch["status"], "PENDING")
        self.assertEqual(patch["new_version"], 2)
        self.assertIn("preview_delta", patch)

    def test_apply_patch_to_registry_updates_unit_version_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "units": [
                            {
                                "id": "payment_retry",
                                "version": 1,
                                "knowledge_delta": {"ops": [{"insert": "支付重试上限为 5 次。"}]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            patch = {
                "patch_id": "kp_20260704_001",
                "unit_id": "payment_retry",
                "commit": "abc123456789",
                "new_version": 2,
                "delta_ops": [{"retain": 8}, {"delete": 1}, {"insert": "3"}],
                "generated_at": "2026-07-04T10:00:00Z",
            }

            registry = apply_patch_to_registry(patch, registry_path)

        unit = registry["units"][0]
        self.assertEqual(unit["version"], 2)
        self.assertEqual(unit["code_hash"], "abc12345")
        self.assertIn("3 次", unit["knowledge_delta"]["ops"][0]["insert"])

    def test_pr_body_and_dry_run_create_pr(self):
        patch = {
            "patch_id": "kp_20260704_001",
            "unit_id": "payment_retry",
            "old_version": 1,
            "new_version": 2,
            "commit": "abc1234",
            "reasoning": "常量更新",
            "affected_files": ["src/payment/retry.py"],
            "delta_ops": [{"insert": "new"}],
        }
        body = build_pr_body(patch)
        pr = create_pr(patch, repo_path=Path("."), dry_run=True)
        self.assertIn("Knowledge Patch", body)
        self.assertEqual(pr["branch"], "knowledge-patch/kp_20260704_001")
        self.assertTrue(pr["dry_run"])

    def test_codeowners_and_rejected_patch_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".github").mkdir()
            (root / ".github" / "CODEOWNERS").write_text("src/payment/* @payments\n", encoding="utf-8")
            patch_path = root / "patch.json"
            patch_path.write_text(json.dumps({"patch_id": "kp_1", "status": "PENDING"}), encoding="utf-8")

            owners = discover_codeowners(root, ["src/payment/retry.py"])
            patch = mark_patch_status(patch_path, "REJECTED", "Needs clearer reasoning.")

        self.assertEqual(owners, ["@payments"])
        self.assertEqual(patch["status"], "REJECTED")
        self.assertEqual(patch["status_reason"], "Needs clearer reasoning.")

    def test_preview_contains_two_editors_and_delta_loader(self):
        preview_path = Path(__file__).resolve().parents[1] / "preview" / "index.html"
        html = preview_path.read_text(encoding="utf-8")
        self.assertIn("cdn.quilljs.com/1.3.6/quill.js", html)
        self.assertIn('id="before"', html)
        self.assertIn('id="after"', html)
        self.assertIn('params.get("delta")', html)


if __name__ == "__main__":
    unittest.main()
