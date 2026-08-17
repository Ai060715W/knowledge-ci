import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import CONFIG_DEFAULTS
from src.discovery.depgraph import build_graph
from src.freshness.check import run_freshness
from src.freshness.layers import (
    ast_semantic_filter,
    changed_symbols,
    impact_analysis,
    layer_time,
)
from src.freshness.llm import VerificationError, judge_freshness, parse_verdict


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def make_unit(**overrides) -> dict:
    unit = {
        "id": "u1",
        "title": "Unit",
        "status": "active",
        "version": 1,
        "scope": {"files": ["mod.py"], "symbols": []},
        "knowledge_delta": {"ops": [{"insert": "old knowledge text"}]},
        "last_verified": None,
        "code_hash": "",
        "evidence": [],
    }
    unit.update(overrides)
    return unit


def write_registry(path: Path, units: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 2, "last_updated": "", "units": units}, ensure_ascii=False),
        encoding="utf-8",
    )


class TimeLayerTest(unittest.TestCase):
    def test_fresh_when_no_commits_after_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            init_repo(root)
            (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
            head = commit(root, "initial")
            verdict = layer_time(root, make_unit(code_hash=head[:8]))
        self.assertTrue(verdict.fresh)
        self.assertIn("code_hash", verdict.reason)

    def test_not_fresh_when_commits_touch_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            init_repo(root)
            (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
            first = commit(root, "initial")
            (root / "mod.py").write_text("X = 2\n", encoding="utf-8")
            commit(root, "change")
            verdict = layer_time(root, make_unit(code_hash=first[:8]))
        self.assertFalse(verdict.fresh)
        self.assertEqual(len(verdict.commits), 1)

    def test_missing_files_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            init_repo(root)
            (root / "other.py").write_text("X = 1\n", encoding="utf-8")
            commit(root, "initial")
            verdict = layer_time(root, make_unit(scope={"files": ["gone.py"], "symbols": []}))
        self.assertFalse(verdict.fresh)
        self.assertEqual(verdict.files_missing, ["gone.py"])

    def test_non_git_directory_degrades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
            verdict = layer_time(root, make_unit())
        self.assertFalse(verdict.fresh)
        self.assertIn("not a git repository", verdict.reason)


class AstLayerTest(unittest.TestCase):
    def assert_semantic(self, old: str, new: str, expected: bool) -> None:
        verdict = ast_semantic_filter({"mod.py": old}, {"mod.py": new})
        self.assertEqual(verdict.semantic, expected, f"old={old!r} new={new!r} -> {verdict.per_file}")

    def test_comment_only_is_noise(self):
        self.assert_semantic("X = 1\n", "# a comment\nX = 1\n", False)

    def test_formatting_only_is_noise(self):
        self.assert_semantic("def f():\n    return 1\n", "def f():\n        return 1\n", False)

    def test_docstring_change_is_noise(self):
        self.assert_semantic('"""one"""\nX = 1\n', '"""two"""\nX = 1\n', False)

    def test_import_reorder_is_noise(self):
        self.assert_semantic("import os\nimport sys\n", "import sys\nimport os\n", False)

    def test_unused_local_rename_is_noise(self):
        self.assert_semantic(
            "def f():\n    x = 1\n    return 2\n",
            "def f():\n    renamed = 1\n    return 2\n",
            False,
        )

    def test_constant_change_is_semantic(self):
        self.assert_semantic("X = 1\n", "X = 2\n", True)

    def test_used_local_change_is_semantic(self):
        self.assert_semantic(
            "def f():\n    x = 1\n    return x\n",
            "def f():\n    x = 2\n    return x\n",
            True,
        )

    def test_parse_failure_is_conservative(self):
        self.assert_semantic("X = 1\n", "def broken(:\n", True)


class ChangedSymbolsTest(unittest.TestCase):
    def test_value_change_flags_symbol(self):
        self.assertEqual(changed_symbols("MAX = 5\n", "MAX = 3\n"), {"MAX"})

    def test_function_body_change_flags_symbol(self):
        self.assertEqual(changed_symbols("def f():\n    return 1\n", "def f():\n    return 2\n"), {"f"})

    def test_unchanged_symbols_empty(self):
        self.assertEqual(changed_symbols("MAX = 5\ndef f():\n    return 1\n", "MAX = 5\ndef f():\n    return 1\n"), set())


class ImpactLayerTest(unittest.TestCase):
    def build_graph_repo(self, temp: str) -> Path:
        root = Path(temp)
        (root / "core.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        (root / "upstream.py").write_text("import core\n", encoding="utf-8")
        (root / "far.py").write_text("import upstream\n", encoding="utf-8")
        (root / "unrelated.py").write_text("X = 1\n", encoding="utf-8")
        return root

    def test_direct_hit_without_symbols(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_graph_repo(temp)
            graph = build_graph(root)
            verdict = impact_analysis(make_unit(), graph, {"mod.py": set()})
        self.assertTrue(verdict.in_scope)

    def test_direct_hit_symbols_intersect(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_graph_repo(temp)
            graph = build_graph(root)
            unit = make_unit(scope={"files": ["mod.py"], "symbols": ["MAX"]})
            verdict = impact_analysis(unit, graph, {"mod.py": {"MAX"}})
        self.assertTrue(verdict.in_scope)

    def test_direct_hit_symbols_disjoint_is_out_of_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_graph_repo(temp)
            graph = build_graph(root)
            unit = make_unit(scope={"files": ["mod.py"], "symbols": ["MAX"]})
            verdict = impact_analysis(unit, graph, {"mod.py": {"OTHER"}})
        self.assertFalse(verdict.in_scope)

    def test_upstream_distance_one_in_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_graph_repo(temp)
            graph = build_graph(root)
            unit = make_unit(scope={"files": ["core.py"], "symbols": []})
            verdict = impact_analysis(unit, graph, {"upstream.py": set()})
        self.assertTrue(verdict.in_scope)
        self.assertTrue(verdict.indirect_hits)

    def test_distance_two_respected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_graph_repo(temp)
            graph = build_graph(root)
            unit = make_unit(scope={"files": ["core.py"], "symbols": []})
            verdict_two = impact_analysis(unit, graph, {"far.py": set()}, depth=2)
            verdict_one = impact_analysis(unit, graph, {"far.py": set()}, depth=1)
        self.assertTrue(verdict_two.in_scope)
        self.assertFalse(verdict_one.in_scope)

    def test_unrelated_module_out_of_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_graph_repo(temp)
            graph = build_graph(root)
            unit = make_unit(scope={"files": ["core.py"], "symbols": []})
            verdict = impact_analysis(unit, graph, {"unrelated.py": set()})
        self.assertFalse(verdict.in_scope)


class LlmLayerTest(unittest.TestCase):
    def test_parse_valid_verdict(self):
        verdict = parse_verdict('{"verdict": "outdated", "reasoning": "已移除"}')
        self.assertEqual(verdict["verdict"], "outdated")

    def test_markdown_fence_rejected(self):
        with self.assertRaises(VerificationError):
            parse_verdict('```json\n{"verdict": "outdated", "reasoning": "x"}\n```')

    def test_bad_enum_rejected(self):
        with self.assertRaises(VerificationError):
            parse_verdict('{"verdict": "maybe", "reasoning": "x"}')

    def test_partial_update_requires_valid_patch_ops(self):
        with self.assertRaises(VerificationError):
            parse_verdict('{"verdict": "partial_update", "reasoning": "x"}')
        verdict = parse_verdict(
            '{"verdict": "partial_update", "reasoning": "改常量", "patch_ops": [{"retain": 1}, {"delete": 1}, {"insert": "2"}]}'
        )
        self.assertEqual(verdict["verdict"], "partial_update")

    def test_fuzzy_wording_rejected(self):
        with self.assertRaises(VerificationError):
            parse_verdict('{"verdict": "outdated", "reasoning": "可能已失效"}')

    def test_judge_with_valid_mock(self):
        unit = make_unit()
        verdict = judge_freshness(
            unit,
            {"commits": [], "ast_summary": [], "impact_reason": "", "diff_excerpts": []},
            model="gpt-4o-mini",
            mock_response='{"verdict": "still_valid", "reasoning": "未受影响"}',
        )
        self.assertEqual(verdict["verdict"], "still_valid")
        self.assertEqual(verdict["attempts"], 1)

    def test_judge_with_invalid_mock_exhausts_attempts(self):
        unit = make_unit()
        with self.assertRaises(RuntimeError):
            judge_freshness(
                unit,
                {"commits": [], "ast_summary": [], "impact_reason": "", "diff_excerpts": []},
                model="gpt-4o-mini",
                mock_response='{"verdict": "maybe", "reasoning": "x"}',
                max_attempts=2,
            )


class RunFreshnessTest(unittest.TestCase):
    def make_repo(self, temp: str):
        root = Path(temp)
        init_repo(root)
        (root / "mod.py").write_text("MAX = 5\n", encoding="utf-8")
        first = commit(root, "initial")
        registry = root / "registry.json"
        return root, first, registry

    def test_fresh_by_time_and_apply_refreshes(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            write_registry(registry, [make_unit(code_hash=first[:8])])
            report, output_path = run_freshness(root, registry, CONFIG_DEFAULTS, out_dir=root / "out", apply=True)
            stored = json.loads(registry.read_text(encoding="utf-8"))
            self.assertTrue(output_path.is_file())
            self.assertEqual(report["summary"]["still_valid"], 1)
            self.assertEqual(report["units"][0]["basis"], "time")
            self.assertTrue(stored["units"][0]["last_verified"])
            self.assertEqual(stored["units"][0]["code_hash"], first[:8])

    def test_semantic_change_without_key_needs_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            write_registry(registry, [make_unit(code_hash=first[:8])])
            (root / "mod.py").write_text("MAX = 3\n", encoding="utf-8")
            commit(root, "change value")
            # Hermetic: mask any ambient OPENAI_API_KEY from the environment.
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                report, _ = run_freshness(root, registry, CONFIG_DEFAULTS, out_dir=root / "out")
        entry = report["units"][0]
        self.assertEqual(entry["verdict"], "needs_llm")
        self.assertEqual(entry["basis"], "no_api_key")
        self.assertEqual(len(entry["layers"]), 3)

    def test_no_llm_flag_stops_after_layer_three(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            write_registry(registry, [make_unit(code_hash=first[:8])])
            (root / "mod.py").write_text("MAX = 3\n", encoding="utf-8")
            commit(root, "change value")
            report, _ = run_freshness(root, registry, CONFIG_DEFAULTS, out_dir=root / "out", no_llm=True)
        self.assertEqual(report["units"][0]["verdict"], "needs_llm")
        self.assertEqual(report["units"][0]["basis"], "no_llm_flag")

    def test_mock_outdated_apply_transitions_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            write_registry(registry, [make_unit(code_hash=first[:8])])
            (root / "mod.py").write_text("MAX = 3\n", encoding="utf-8")
            commit(root, "change value")
            mock = '{"verdict": "outdated", "reasoning": "常量语义已改变"}'
            report, _ = run_freshness(
                root, registry, CONFIG_DEFAULTS, out_dir=root / "out",
                apply=True, mock_response=mock,
            )
            stored = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(report["units"][0]["verdict"], "outdated")
        self.assertEqual(stored["units"][0]["status"], "outdated")

    def test_mock_partial_update_auto_patch_writes_pending_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            unit = make_unit(code_hash=first[:8], knowledge_delta={"ops": [{"insert": "MAX is 5"}]})
            write_registry(registry, [unit])
            (root / "mod.py").write_text("MAX = 3\n", encoding="utf-8")
            commit(root, "change value")
            mock = (
                '{"verdict": "partial_update", "reasoning": "常量改为 3",'
                ' "patch_ops": [{"delete": 8}, {"insert": "MAX is 3"}]}'
            )
            patches = root / "patches"
            report, _ = run_freshness(
                root, registry, CONFIG_DEFAULTS, out_dir=root / "out",
                auto_patch=True, mock_response=mock, patches_path=patches,
            )
            patch_files = list(patches.glob("patch_*.json"))
            stored = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(report["units"][0]["verdict"], "partial_update")
            self.assertEqual(len(patch_files), 1)
            patch = json.loads(patch_files[0].read_text(encoding="utf-8"))
            self.assertEqual(patch["status"], "PENDING")
            self.assertEqual(patch["unit_id"], "u1")
            self.assertEqual(stored["units"][0]["status"], "active")  # never auto-landed

    def test_mock_new_knowledge_produces_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            write_registry(registry, [make_unit(code_hash=first[:8])])
            (root / "mod.py").write_text("MAX = 3\n", encoding="utf-8")
            commit(root, "change value")
            mock = (
                '{"verdict": "new_knowledge", "reasoning": "产生新约束",'
                ' "new_knowledge": {"title": "MAX 不可超过 3", "summary": "MAX 上限为 3",'
                ' "rationale": "来自变更", "symbols": ["MAX"]}}'
            )
            report, _ = run_freshness(
                root, registry, CONFIG_DEFAULTS, out_dir=root / "out", mock_response=mock,
            )
        self.assertEqual(report["units"][0]["verdict"], "new_knowledge")
        draft = report["candidate_drafts"][0]
        self.assertEqual(draft["status"], "proposed")
        self.assertEqual(draft["title"], "MAX 不可超过 3")
        # The alias lets kc ask-owner consume freshness drafts directly.
        self.assertEqual(report["candidates"], report["candidate_drafts"])

    def test_missing_files_marks_outdated(self):
        with tempfile.TemporaryDirectory() as temp:
            root, first, registry = self.make_repo(temp)
            write_registry(registry, [make_unit(code_hash=first[:8], scope={"files": ["gone.py"], "symbols": []})])
            report, _ = run_freshness(root, registry, CONFIG_DEFAULTS, out_dir=root / "out")
        self.assertEqual(report["units"][0]["verdict"], "outdated")
        self.assertEqual(report["units"][0]["basis"], "files_missing")


if __name__ == "__main__":
    unittest.main()
