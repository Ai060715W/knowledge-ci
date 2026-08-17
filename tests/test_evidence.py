import subprocess
import tempfile
import unittest
from pathlib import Path

from src.discovery.evidence import module_history, symbol_history


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


class EvidenceTest(unittest.TestCase):
    def make_repo(self, temp: str) -> Path:
        root = Path(temp)
        init_repo(root)
        (root / "mod.py").write_text("MAX_RETRY = 5\n", encoding="utf-8")
        commit(root, "introduce retry module")
        (root / "mod.py").write_text("MAX_RETRY = 3\n", encoding="utf-8")
        commit(root, "lower retry limit")
        (root / "mod.py").write_text("MAX_RETRY = 5\n", encoding="utf-8")
        commit(root, "Revert limit change")
        return root

    def test_module_history_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            evidence = module_history(root, "mod.py")
        self.assertEqual(len(evidence), 3)
        self.assertEqual(evidence[0]["role"], "reverted")
        self.assertEqual(evidence[-1]["role"], "introduced")
        for item in evidence:
            self.assertEqual(item["type"], "commit")
            self.assertTrue(item["id"])
            self.assertTrue(item["short_id"])

    def test_symbol_history_pickaxe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            evidence = symbol_history(root, "mod.py", "MAX_RETRY")
        self.assertEqual(len(evidence), 3)
        self.assertTrue(all(item["id"] for item in evidence))

    def test_unknown_symbol_returns_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            evidence = symbol_history(root, "mod.py", "NOT_PRESENT")
        self.assertEqual(evidence, [])

    def test_non_git_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
            self.assertEqual(module_history(root, "mod.py"), [])
            self.assertEqual(symbol_history(root, "mod.py", "X"), [])


if __name__ == "__main__":
    unittest.main()
