import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.metrics.metrics import (
    compute_confirmation_rate,
    compute_coverage,
    compute_freshness_rate,
    compute_hit_rate,
    compute_metrics,
    status_distribution,
)


def write_registry(path: Path, units: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 2, "last_updated": "", "units": units}, ensure_ascii=False),
        encoding="utf-8",
    )


def make_unit(unit_id: str, files: list[str], status: str = "active", code_hash: str = "") -> dict:
    return {
        "id": unit_id,
        "title": unit_id,
        "status": status,
        "version": 1,
        "scope": {"files": files, "symbols": []},
        "knowledge_delta": {"ops": [{"insert": "k"}]},
        "code_hash": code_hash,
        "evidence": [],
    }


class CoverageTest(unittest.TestCase):
    def test_coverage_ratio_and_missing_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            write_registry(
                registry_path,
                [
                    make_unit("u1", ["src/core.py"]),
                    make_unit("u2", ["src/other.py"]),
                ],
            )
            report = {"top_modules": [{"path": "src/core.py"}, {"path": "src/missing.py"}]}
            metric = compute_coverage(json.loads(registry_path.read_text(encoding="utf-8")), report, registry_path)
        self.assertEqual(metric["value"], 0.5)
        self.assertEqual(metric["numerator"], 1)
        self.assertEqual(metric["denominator"], 2)

        metric = compute_coverage(None, None, None)
        self.assertIsNone(metric["value"])

    def test_inactive_unit_does_not_count_as_covered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            write_registry(registry_path, [make_unit("u1", ["src/core.py"], status="outdated")])
            report = {"top_modules": [{"path": "src/core.py"}]}
            metric = compute_coverage(json.loads(registry_path.read_text(encoding="utf-8")), report, registry_path)
        self.assertEqual(metric["value"], 0.0)


class FreshnessRateTest(unittest.TestCase):
    def test_freshness_rate_uses_git_anchors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "t@e.c"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            (root / "b.py").write_text("Y = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            (root / "a.py").write_text("X = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=root, check=True)

            registry = {
                "version": 2,
                "units": [
                    make_unit("fresh", ["b.py"], code_hash=head[:8]),
                    make_unit("stale", ["a.py"], code_hash=head[:8]),
                ],
            }
            metric = compute_freshness_rate(registry, root)
        self.assertEqual(metric["value"], 0.5)

    def test_no_active_units(self):
        metric = compute_freshness_rate({"version": 2, "units": []}, Path("."))
        self.assertIsNone(metric["value"])
        self.assertIn("no active", metric["note"])


class HitRateTest(unittest.TestCase):
    def test_hit_rate_from_feedback_log(self):
        with tempfile.TemporaryDirectory() as temp:
            feedback = Path(temp) / "feedback.jsonl"
            records = [
                {"feedback": "useful", "adopted": True},
                {"feedback": "useful"},
                {"feedback": "improve", "adopted": False},
                {"feedback": "useful", "adopted": True},
            ]
            feedback.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                encoding="utf-8",
            )
            metric = compute_hit_rate(feedback)
        self.assertEqual(metric["value"], 0.5)
        self.assertEqual(metric["numerator"], 2)
        self.assertEqual(metric["denominator"], 4)

    def test_missing_log(self):
        metric = compute_hit_rate(None)
        self.assertIsNone(metric["value"])


class ConfirmationRateTest(unittest.TestCase):
    def test_applied_over_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            patches = Path(temp)
            for index, status in enumerate(("APPLIED", "APPLIED", "REJECTED", "PENDING")):
                (patches / f"patch_kp_00{index}.json").write_text(
                    json.dumps({"status": status}), encoding="utf-8"
                )
            metric = compute_confirmation_rate(patches)
        self.assertAlmostEqual(metric["value"], 2 / 3, places=4)
        self.assertEqual(metric["denominator"], 3)

    def test_no_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            metric = compute_confirmation_rate(Path(temp))
        self.assertIsNone(metric["value"])


class ComputeMetricsTest(unittest.TestCase):
    def test_full_report_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "t@e.c"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

            registry_path = root / "registry.json"
            write_registry(registry_path, [make_unit("u1", ["a.py"], code_hash="")])
            reports = root / "reports"
            reports.mkdir()
            (reports / "discovery_20260817_000000.json").write_text(
                json.dumps({"top_modules": [{"path": "a.py"}]}), encoding="utf-8"
            )
            patches = root / "patches"
            patches.mkdir()
            (patches / "patch_kp_001.json").write_text(json.dumps({"status": "APPLIED"}), encoding="utf-8")
            feedback = root / "feedback.jsonl"
            feedback.write_text(json.dumps({"feedback": "useful", "adopted": True}) + "\n", encoding="utf-8")

            report, output_path = compute_metrics(
                root, registry_path, reports_path=reports, patches_path=patches,
                feedback_path=feedback, out_dir=root / "metrics",
            )
        self.assertTrue(output_path.name == "metrics.json")
        keys = [metric["key"] for metric in report["metrics"]]
        self.assertEqual(keys, ["coverage", "freshness_rate", "hit_rate", "confirmation_rate"])
        for metric in report["metrics"]:
            self.assertIn("formula", metric)
            self.assertIn("numerator", metric)
            self.assertIn("denominator", metric)
            self.assertIsNotNone(metric["value"])
        self.assertEqual(report["status_distribution"]["counts"], {"active": 1})

    def test_status_distribution_counts_legacy_as_active(self):
        distribution = status_distribution(
            {"version": 2, "units": [{"id": "a"}, {"id": "b", "status": "retired"}]}
        )
        self.assertEqual(distribution["counts"], {"active": 1, "retired": 1})


if __name__ == "__main__":
    unittest.main()
