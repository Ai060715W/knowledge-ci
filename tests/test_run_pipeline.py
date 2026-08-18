import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.agents.orchestrator import PIPELINE_ORDER, run_pipeline


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def commit(root: Path, message: str = "init") -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def write_registry(path: Path, unit_id: str, files: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "units": [
                    {
                        "id": unit_id,
                        "title": unit_id,
                        "status": "active",
                        "version": 1,
                        "scope": {"files": files, "symbols": []},
                        "knowledge_delta": {"ops": [{"insert": "old knowledge"}]},
                        "evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class RunPipelineTest(unittest.TestCase):
    def make_repo(self, temp: str) -> Path:
        root = Path(temp)
        init_repo(root)
        (root / "src").mkdir()
        # "legacy" naming triggers the bridge_compat signal so a draft lands
        # on this file, and the registry unit matches it -> patch proposal.
        (root / "src" / "legacy.py").write_text(
            "class Client:\n    pass\n\nclient = Client()\n\nTHRESHOLD = 300\n",
            encoding="utf-8",
        )
        commit(root)
        return root

    def test_full_pipeline_on_managed_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            registry = root / "registry.json"
            write_registry(registry, "legacy_unit", ["src/legacy.py"])
            patches = root / "patches"
            report, output_path = run_pipeline(
                root, out_dir=root / "out", registry_path=registry, patches_path=patches
            )
            self.assertTrue(output_path.is_file())
            self.assertEqual(report["summary"]["analysis"], "ok")
            self.assertEqual(report["summary"]["injection"], "ok")
            self.assertEqual(len(report["agents"]), 6)
            drafts = report["drafts"]
            self.assertTrue(drafts)
            proposals = [item for group in report["proposals"] for item in group.get("proposals", [])]
            self.assertGreaterEqual(len(proposals), 1)
            patch_file = Path(proposals[0]["path"])
            patch = json.loads(patch_file.read_text(encoding="utf-8"))
            self.assertEqual(patch["status"], "PENDING")
            self.assertEqual(patch["unit_id"], "legacy_unit")
            self.assertTrue(report["reviews"])
            self.assertTrue(report["injection_previews"])

    def test_stop_after_knowledge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            report, _ = run_pipeline(root, out_dir=root / "out", stop_after="knowledge")
        statuses = report["summary"]
        self.assertEqual(statuses["knowledge"], "ok")
        self.assertEqual(statuses.get("review"), "skipped")
        self.assertEqual(statuses.get("injection"), "skipped")

    def test_no_registry_yields_zero_proposals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            report, _ = run_pipeline(root, out_dir=root / "out")
        patch_stage = next(stage for stage in report["pipeline"] if stage["name"] == "patch")
        self.assertEqual(patch_stage["status"], "ok")
        self.assertEqual(patch_stage["proposals"], [])

    def test_non_git_directory_degrades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mod.py").write_text("def f(n):\n    if n > 300:\n        return n\n", encoding="utf-8")
            report, _ = run_pipeline(root, out_dir=root / "out")
        self.assertEqual(report["summary"]["analysis"], "ok")
        self.assertIsNone(report["head_commit"])

    def test_pipeline_order_matches_contract(self):
        self.assertEqual(
            PIPELINE_ORDER,
            ("analysis", "evidence", "knowledge", "risk", "patch", "review", "injection"),
        )


if __name__ == "__main__":
    unittest.main()
