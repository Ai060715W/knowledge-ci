import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.config import CONFIG_DEFAULTS
from src.discovery.discover import run_discovery


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


class DiscoverTest(unittest.TestCase):
    def make_repo(self, temp: str) -> Path:
        root = Path(temp)
        init_repo(root)
        (root / "payment").mkdir()
        (root / "payment" / "retry.py").write_text(
            "MAX_RETRY = 3\n\n"
            "class RetryClient:\n    pass\n\n"
            "client = RetryClient()\n\n"
            "def should_retry(attempt):\n"
            "    if attempt > 300:\n"
            "        return False\n"
            "    return True\n",
            encoding="utf-8",
        )
        (root / "payment" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        commit(root, "initial payment module")
        (root / "payment" / "retry.py").write_text(
            "MAX_RETRY = 5\n\n"
            "class RetryClient:\n    pass\n\n"
            "client = RetryClient()\n\n"
            "def should_retry(attempt):\n"
            "    if attempt > 300:\n"
            "        return False\n"
            "    return True\n",
            encoding="utf-8",
        )
        commit(root, "bump retry limit")
        return root

    def test_end_to_end_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            out = Path(temp) / "reports"
            report, output_path = run_discovery(root, CONFIG_DEFAULTS, out_dir=out)
            self.assertTrue(output_path.name.startswith("discovery_"))
            self.assertEqual(report["modules_scanned"], 2)
            self.assertEqual(report["git"], True)
            self.assertIsNotNone(report["head_commit"])
            self.assertGreaterEqual(len(report["top_modules"]), 1)
            top = report["top_modules"][0]
            self.assertEqual(top["module"], "payment.retry")
            self.assertTrue(any(s["kind"] == "magic_number" for s in top["signals"]))
            self.assertTrue(any(s["kind"] == "global_instance" for s in top["signals"]))
            self.assertTrue(report["candidate_count"] >= 1)
            for candidate in report["candidates"]:
                self.assertEqual(candidate["status"], "proposed")
                self.assertIn("signal_kind", candidate)
                # Git-backed candidates carry computed confidence and questions.
                self.assertIsNotNone(candidate["confidence"])
                self.assertGreaterEqual(candidate["confidence"], 0.0)
                self.assertLessEqual(candidate["confidence"], 1.0)
                self.assertTrue(candidate["questions"])
            # Top modules carry owner inference.
            self.assertIn("owners", report["top_modules"][0])
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["candidate_count"], report["candidate_count"])

    def test_registry_annotation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "payment_retry",
                                "title": "Retry",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["payment/retry.py"], "symbols": []},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report, _ = run_discovery(
                root, CONFIG_DEFAULTS, out_dir=Path(temp) / "reports", registry_path=registry_path
            )
        annotated = [entry for entry in report["top_modules"] if entry.get("existing_unit")]
        self.assertEqual(annotated[0]["existing_unit"], "payment_retry")

    def test_no_python_files_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "notes.md").write_text("no code here\n", encoding="utf-8")
            report, output_path = run_discovery(root, CONFIG_DEFAULTS, out_dir=Path(temp) / "reports")
            self.assertEqual(report["modules_scanned"], 0)
            self.assertEqual(report["candidates"], [])
            self.assertIn("No Python", report["note"])
            self.assertTrue(output_path.is_file())

    def test_non_git_directory_degrades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mod.py").write_text("def f(n):\n    if n > 300:\n        return n\n", encoding="utf-8")
            report, _ = run_discovery(root, CONFIG_DEFAULTS, out_dir=Path(temp) / "reports")
        self.assertFalse(report["git"])
        self.assertIsNone(report["head_commit"])
        self.assertEqual(report["modules_scanned"], 1)
        self.assertTrue(any(s["kind"] == "magic_number" for s in report["top_modules"][0]["signals"]))

    def test_graph_cache_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            out = Path(temp) / "reports"
            first, _ = run_discovery(root, CONFIG_DEFAULTS, out_dir=out)
            second_report, _ = run_discovery(root, CONFIG_DEFAULTS, out_dir=out, use_cache=True)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second_report["cache_hit"])

    def test_exclude_paths_setting_drops_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            init_repo(root)
            (root / "tests").mkdir()
            (root / "tests" / "noise.py").write_text("X = 1\n", encoding="utf-8")
            (root / "app").mkdir()
            (root / "app" / "core.py").write_text("Y = 1\n", encoding="utf-8")
            commit(root, "add modules")
            settings = {"discovery": {**CONFIG_DEFAULTS["discovery"], "exclude_paths": ["tests"]}}
            report, _ = run_discovery(root, settings, out_dir=Path(temp) / "reports")
            scanned = {entry["module"] for entry in report["top_modules"]}
        self.assertIn("app.core", scanned)
        self.assertNotIn("tests.noise", scanned)

    def test_candidate_drafts_validate_as_v2_units(self):
        from src.registry.schema import validate_unit

        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            report, _ = run_discovery(root, CONFIG_DEFAULTS, out_dir=Path(temp) / "reports")
        for candidate in report["candidates"]:
            self.assertEqual(validate_unit(candidate), [])


if __name__ == "__main__":
    unittest.main()
