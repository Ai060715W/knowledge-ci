import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.inject.context import (
    build_context,
    estimate_tokens,
    format_context,
    format_unmatched,
    knowledge_block,
    record_feedback,
    resolve_input_path,
    truncate_text,
)


def make_registry(root: Path, **unit_overrides) -> Path:
    unit = {
        "id": "payment_retry",
        "name": "支付重试",
        "file_pattern": "src/payment/retry.py",
        "risk_level": "HIGH",
        "knowledge_delta": {"ops": [{"insert": "支付重试上限为 3 次，超时后触发补偿流程。"}]},
        "related_docs": ["docs/payment.md"],
        "last_verified": "2026-07-07",
        "code_hash": "36e4a824",
        "version": 1,
    }
    unit.update(unit_overrides)
    registry_path = root / ".knowledge-ci" / "data" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"version": 1, "units": [unit]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return registry_path


def write_config(root: Path) -> Path:
    config_path = root / ".knowledge-ci" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "project_path: '..'\n"
        "registry_path: data/registry.json\n"
        "reports_path: data/reports\n"
        "patches_path: data/patches\n"
        "feedback_path: data/feedback.jsonl\n"
        "model: deepseek-chat\n",
        encoding="utf-8",
    )
    return config_path


class InjectContextTest(unittest.TestCase):
    def test_resolve_input_path_variants(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "myapp"
            cases = [
                ("myapp/src/payment/retry.py", "src/payment/retry.py"),
                ("./src/payment/retry.py", "src/payment/retry.py"),
                ("src/payment/retry.py", "src/payment/retry.py"),
                ("src\\payment\\retry.py", "src/payment/retry.py"),
            ]
            for raw, expected in cases:
                with self.subTest(raw=raw):
                    self.assertEqual(resolve_input_path(raw, root), expected)

    def test_resolve_absolute_path_under_project(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "myapp" / "src" / "payment").mkdir(parents=True)
            absolute = str((base / "myapp" / "src" / "payment" / "retry.py").resolve())
            self.assertEqual(resolve_input_path(absolute, base / "myapp"), "src/payment/retry.py")

    def test_build_context_matched_renders_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = make_registry(root)
            context = build_context(
                file_path="src/payment/retry.py",
                project_root=root,
                registry_path=registry_path,
                reports_path=root / ".knowledge-ci" / "data" / "reports",
                patches_path=root / ".knowledge-ci" / "data" / "patches",
            )
        self.assertTrue(context["matched"])
        self.assertEqual(context["unit_id"], "payment_retry")
        self.assertIn("3 次", context["knowledge_summary"])
        self.assertEqual(context["risk_level"], "HIGH")
        self.assertEqual(context["last_verified"], "2026-07-07")

    def test_build_context_unmatched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = make_registry(root)
            context = build_context(
                "src/utils/helper.py",
                root,
                registry_path=registry_path,
            )
        self.assertFalse(context["matched"])
        self.assertEqual(context["file_path"], "src/utils/helper.py")
        self.assertIn("暂无知识记录", format_unmatched(context["file_path"]))

    def test_history_uses_applied_patches_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = make_registry(root)
            patches = root / ".knowledge-ci" / "data" / "patches"
            patches.mkdir(parents=True)
            (patches / "patch_kp_a.json").write_text(
                json.dumps(
                    {
                        "patch_id": "kp_a",
                        "unit_id": "payment_retry",
                        "status": "APPLIED",
                        "reasoning": "重试次数从 5 调整为 3",
                        "generated_at": "2026-07-04T10:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            (patches / "patch_kp_b.json").write_text(
                json.dumps(
                    {
                        "patch_id": "kp_b",
                        "unit_id": "payment_retry",
                        "status": "PENDING",
                        "reasoning": "不应出现在注入内容中",
                        "generated_at": "2026-07-05T10:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            context = build_context(
                "src/payment/retry.py",
                root,
                registry_path=registry_path,
                patches_path=patches,
            )
        history = context["history_decisions"]
        self.assertEqual(len(history), 1)
        self.assertIn("重试次数从 5 调整为 3", history[0])
        self.assertNotIn("不应出现", "\n".join(history))

    def test_impact_warnings_from_latest_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = make_registry(root)
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["units"].append(
                {
                    "id": "payment_refund",
                    "name": "支付退款",
                    "file_pattern": "src/payment/refund.py",
                    "risk_level": "HIGH",
                }
            )
            registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

            reports = root / ".knowledge-ci" / "data" / "reports"
            reports.mkdir(parents=True)
            (reports / "impact_old.json").write_text(
                json.dumps(
                    {
                        "commit": "old",
                        "commit_short": "old00000",
                        "generated_at": "2026-07-01T00:00:00Z",
                        "affected_units": ["payment_retry"],
                    }
                ),
                encoding="utf-8",
            )
            (reports / "impact_new.json").write_text(
                json.dumps(
                    {
                        "commit": "new",
                        "commit_short": "new00000",
                        "generated_at": "2026-07-05T00:00:00Z",
                        "affected_units": ["payment_retry", "payment_refund"],
                    }
                ),
                encoding="utf-8",
            )
            context = build_context(
                "src/payment/retry.py",
                root,
                registry_path=registry_path,
                reports_path=reports,
            )
        warnings = context["impact_warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("payment_refund", warnings[0])
        self.assertIn("支付退款", warnings[0])
        self.assertIn("new00000", warnings[0])

    def test_truncate_text_obeys_budget(self):
        long_text = "支付规则说明。" * 200
        truncated, was_truncated = truncate_text(long_text, 100)
        self.assertTrue(was_truncated)
        self.assertLessEqual(estimate_tokens(truncated), 100)
        self.assertTrue(truncated.endswith("…"))

    def test_format_context_compresses_long_summary_to_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            long_summary = "支付重试上限为 3 次，超时后触发补偿流程。" * 100
            registry_path = make_registry(root, knowledge_delta={"ops": [{"insert": long_summary}]})
            context = build_context(
                "src/payment/retry.py",
                root,
                registry_path=registry_path,
                reports_path=root / ".knowledge-ci" / "data" / "reports",
                patches_path=root / ".knowledge-ci" / "data" / "patches",
            )
            block, tokens = knowledge_block(context, max_tokens=300)
            output = format_context(context, max_tokens=300, include_feedback=False)
        self.assertLessEqual(tokens, 300)
        self.assertIn("…", block)
        self.assertIn("【Knowledge CI 上下文】", output)
        self.assertIn("风险等级：HIGH", output)
        self.assertNotIn("feedback=", output)

    def test_format_context_includes_feedback_links(self):
        context = {
            "matched": True,
            "file_path": "src/payment/retry.py",
            "unit_id": "payment_retry",
            "unit_name": "支付重试",
            "risk_level": "HIGH",
            "knowledge_summary": "支付重试上限为 3 次。",
            "history_decisions": [],
            "impact_warnings": [],
            "related_docs": [],
            "last_verified": "2026-07-07",
            "version": 1,
            "code_hash": "abc",
        }
        output = format_context(context, base_url="http://localhost:8080/")
        self.assertIn("feedback=useful", output)
        self.assertIn("feedback=improve", output)
        self.assertIn("unit_id=payment_retry", output)
        self.assertIn("最近验证：2026-07-07", output)

    def test_record_feedback_writes_jsonl_and_validates(self):
        with tempfile.TemporaryDirectory() as temp:
            feedback_path = Path(temp) / "feedback.jsonl"
            record = record_feedback(feedback_path, "payment_retry", "src/payment/retry.py", "useful")
            self.assertEqual(record["feedback"], "useful")
            with self.assertRaises(ValueError):
                record_feedback(feedback_path, "payment_retry", "src/payment/retry.py", "bad")

            lines = feedback_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["unit_id"], "payment_retry")
            self.assertEqual(parsed["source"], "web")
            self.assertIn("created_at", parsed)

    def test_feedback_server_round_trip(self):
        from scripts.feedback_server import create_server

        with tempfile.TemporaryDirectory() as temp:
            feedback_file = Path(temp) / "feedback.jsonl"
            server = create_server("127.0.0.1", 0, feedback_file)
            host, port = server.server_address
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://{host}:{port}"
                with urllib.request.urlopen(f"{base}/feedback?unit_id=u1&file=f.py&feedback=useful", timeout=10) as response:
                    body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                self.assertIn("useful", body)

                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(f"{base}/feedback?feedback=bad", timeout=10)
                self.assertEqual(raised.exception.code, 400)

                with urllib.request.urlopen(f"{base}/", timeout=10) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("quill", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            lines = feedback_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["unit_id"], "u1")

    def test_cli_output_for_managed_and_unmanaged_files(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "inject_context.py"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_registry(root)
            config_path = write_config(root)
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
            common = [sys.executable, str(script), "--config", str(config_path)]
            managed = subprocess.run(
                [*common, "--file", "src/payment/retry.py"],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                cwd=root,
            )
            unmanaged = subprocess.run(
                [*common, "--file", "src/utils/helper.py"],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                cwd=root,
            )
            as_json = subprocess.run(
                [*common, "--file", "src/payment/retry.py", "--json"],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                cwd=root,
            )

        self.assertEqual(managed.returncode, 0, managed.stderr)
        self.assertIn("【Knowledge CI 上下文】", managed.stdout)
        self.assertIn("payment_retry", managed.stdout)
        self.assertIn("feedback=useful", managed.stdout)

        self.assertEqual(unmanaged.returncode, 0, unmanaged.stderr)
        self.assertIn("暂无知识记录", unmanaged.stdout)

        payload = json.loads(as_json.stdout)
        self.assertTrue(payload["matched"])
        self.assertIn("estimated_tokens", payload)

    def test_cursor_rules_template_and_vscode_task_exist(self):
        root = Path(__file__).resolve().parents[1]
        rules = root / "templates" / "cursor-knowledge-ci.mdc"
        self.assertTrue(rules.exists())
        rules_text = rules.read_text(encoding="utf-8")
        self.assertIn("inject_context.py", rules_text)
        self.assertIn("globs", rules_text)

        tasks_path = root / ".vscode" / "tasks.json"
        payload = json.loads(tasks_path.read_text(encoding="utf-8"))
        labels = [task.get("label") for task in payload.get("tasks", [])]
        self.assertIn("Knowledge CI: Inject Context", labels)


if __name__ == "__main__":
    unittest.main()
