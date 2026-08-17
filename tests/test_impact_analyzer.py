import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.impact.analyzer import analyze_commit, normalize_repo_path, summarize_change, write_report


class ImpactAnalyzerTest(unittest.TestCase):
    def test_normalize_repo_path_preserves_hidden_directory_name(self):
        self.assertEqual(normalize_repo_path("./.github/pull_request_template.md"), ".github/pull_request_template.md")

    def test_python_summary_maps_changed_line_to_enclosing_function(self):
        old_source = """MAX_RETRY = 5\n\nclass Worker:\n    def run(self):\n        return MAX_RETRY\n"""
        new_source = """MAX_RETRY = 3\n\nclass Worker:\n    def run(self):\n        return MAX_RETRY + 1\n"""
        patch_text = """@@ -1,5 +1,5 @@\n-MAX_RETRY = 5\n+MAX_RETRY = 3\n \n class Worker:\n     def run(self):\n-        return MAX_RETRY\n+        return MAX_RETRY + 1\n"""

        summary = summarize_change("src/payment/retry.py", patch_text, old_source, new_source)

        self.assertEqual(summary["functions"], ["run"])
        self.assertEqual(summary["classes"], ["Worker"])
        self.assertEqual(summary["constants"], ["MAX_RETRY"])

    def test_analyze_commit_writes_affected_and_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            (repo / "src" / "payment").mkdir(parents=True)
            (repo / "src" / "utils").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "src" / "payment" / "retry.py").write_text(
                "MAX_RETRY = 5\n\ndef retry_payment():\n    return MAX_RETRY\n",
                encoding="utf-8",
            )
            (repo / "src" / "utils" / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
            (repo / "docs" / "retry.md").write_text("retry_payment uses MAX_RETRY.\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

            (repo / "src" / "payment" / "retry.py").write_text(
                "MAX_RETRY = 3\n\ndef retry_payment():\n    return MAX_RETRY + 1\n",
                encoding="utf-8",
            )
            (repo / "src" / "utils" / "helper.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "change retry"], cwd=repo, check=True, capture_output=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            registry_path = root / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "units": [
                            {
                                "id": "payment_retry",
                                "file_pattern": "src/payment/retry.py",
                                "risk_level": "HIGH",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = analyze_commit(commit, repo, registry_path)
            output_path = write_report(report, root / "reports")

        self.assertTrue(output_path.name.startswith("impact_"))
        self.assertEqual(report["affected_units"], ["payment_retry"])
        self.assertEqual(report["unmanaged_files"], ["src/utils/helper.py"])
        self.assertEqual(len(report["changed_files"]), 2)
        self.assertTrue(
            any(item["symbol"] == "MAX_RETRY" and "docs/retry.md" in item["docs"] for item in report["related_docs_suggestions"])
        )

    def test_analyze_initial_commit_marks_files_as_added(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            (repo / "src").mkdir()
            (repo / "src" / "service.py").write_text("def start():\n    return True\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            registry_path = root / "registry.json"
            registry_path.write_text(
                json.dumps({"version": 1, "units": [{"id": "service", "file_pattern": "src/service.py"}]}),
                encoding="utf-8",
            )

            report = analyze_commit(commit, repo, registry_path, include_related_docs=False)

        self.assertEqual(report["changed_files"][0]["status"], "added")
        self.assertEqual(report["affected_units"], ["service"])


if __name__ == "__main__":
    unittest.main()
