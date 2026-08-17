import subprocess
import tempfile
import unittest
from pathlib import Path

from src.discovery.depgraph import build_graph
from src.discovery.scoring import collect_commit_stats, compute_scores, score_modules


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "base@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Base User"], cwd=root, check=True)


def commit_as(root: Path, name: str, email: str, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", message],
        cwd=root,
        check=True,
    )


class ScoringTest(unittest.TestCase):
    def make_repo(self, temp: str) -> Path:
        root = Path(temp)
        init_repo(root)
        (root / "hot").mkdir()
        (root / "cold").mkdir()
        (root / "hot" / "core.py").write_text("import cold.util\nX = 1\n", encoding="utf-8")
        (root / "cold" / "util.py").write_text("Y = 1\n", encoding="utf-8")
        commit_as(root, "A", "a@example.com", "initial import")
        (root / "hot" / "core.py").write_text("import cold.util\nX = 2\n", encoding="utf-8")
        commit_as(root, "B", "b@example.com", "tweak core")
        (root / "hot" / "core.py").write_text("import cold.util\nX = 3\n", encoding="utf-8")
        commit_as(root, "A", "a@example.com", "fix crash in core")
        (root / "hot" / "core.py").write_text("import cold.util\nX = 1\n", encoding="utf-8")
        commit_as(root, "B", "b@example.com", "Revert accidental change")
        return root

    def test_commit_stats_count_frequency_reverts_incidents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            graph = build_graph(root)
            stats, is_git = collect_commit_stats(root, graph)
        self.assertTrue(is_git)
        self.assertEqual(stats["hot.core"]["commits"], 4)
        self.assertEqual(stats["hot.core"]["reverts"], 1)
        self.assertEqual(stats["hot.core"]["incidents"], 1)
        self.assertEqual(stats["cold.util"]["commits"], 1)
        self.assertIsNotNone(stats["hot.core"]["introduced_at"])

    def test_contributor_entropy_rewards_multiple_authors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            graph = build_graph(root)
            scores = compute_scores(graph, collect_commit_stats(root, graph)[0])
            by_module = {entry["module"]: entry for entry in scores}
        self.assertGreater(by_module["hot.core"]["factors"]["contributor_entropy"], 0)
        self.assertEqual(by_module["cold.util"]["factors"]["contributor_entropy"], 0.0)

    def test_scores_sorted_and_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            graph = build_graph(root)
            scores = compute_scores(graph, collect_commit_stats(root, graph)[0])
        self.assertEqual(scores[0]["module"], "hot.core")
        for entry in scores:
            for factor_value in entry["factors"].values():
                self.assertGreaterEqual(factor_value, 0.0)
                self.assertLessEqual(factor_value, 1.0)

    def test_weights_from_settings_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            graph = build_graph(root)
            zero_change = {
                "discovery": {
                    "weights": {
                        "change_frequency": 0.0,
                        "dependency_centrality": 1.0,
                        "incident_history": 0.0,
                        "rollback_count": 0.0,
                        "contributor_entropy": 0.0,
                        "cross_layer_impact": 0.0,
                    }
                }
            }
            scores = compute_scores(graph, collect_commit_stats(root, graph)[0], zero_change)
            by_module = {entry["module"]: entry for entry in scores}
        # Weights change the score, not the normalized factor values: with only
        # centrality weighted, the score equals the centrality factor exactly.
        self.assertAlmostEqual(
            by_module["hot.core"]["score"],
            by_module["hot.core"]["factors"]["dependency_centrality"],
        )
        self.assertEqual(by_module["hot.core"]["factors"]["change_frequency"], 1.0)

    def test_non_git_directory_degrades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
            graph = build_graph(root)
            scores, stats, is_git = score_modules(root, graph, top_k=10)
        self.assertFalse(is_git)
        self.assertEqual(scores[0]["module"], "mod")
        self.assertEqual(stats, {})

    def test_top_k_limits_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_repo(temp)
            graph = build_graph(root)
            scores, _, _ = score_modules(root, graph, top_k=1)
        self.assertEqual(len(scores), 1)


if __name__ == "__main__":
    unittest.main()
