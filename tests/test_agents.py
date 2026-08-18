import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.agents.base import AGENTS, Agent, describe_agents, register
from src.agents.analysis import AnalysisAgent
from src.agents.evidence import EvidenceAgent
from src.agents.injection import InjectionAgent
from src.agents.knowledge import KnowledgeAgent
from src.agents.review import ReviewAgent
from src.agents.risk import RiskAgent


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def make_candidate(**overrides):
    candidate = {
        "id": "cand_x_001",
        "signal_kind": "magic_number",
        "title": "魔法数字 / magic number: x",
        "summary": "magic number 300",
        "rationale": "inferred",
        "scope": {"files": ["x.py"], "symbols": []},
        "evidence": [
            {"type": "commit", "id": "abc", "role": "introduced"},
            {"type": "commit", "id": "def", "role": "modified"},
        ],
        "confidence": 0.51,
        "owner": "A <a@example.com>",
        "owner_inferred": False,
        "reviewer": None,
        "status": "proposed",
        "knowledge_delta": {"ops": [{"insert": "magic number 300"}]},
        "related_docs": [],
        "last_verified": None,
        "code_hash": "",
        "version": 1,
        "questions": [],
    }
    candidate.update(overrides)
    return candidate


class ProtocolTest(unittest.TestCase):
    def test_six_agents_registered_with_schemas(self):
        self.assertEqual(
            set(AGENTS),
            {"analysis", "evidence", "knowledge", "risk", "review", "injection"},
        )
        for name, agent_class in AGENTS.items():
            with self.subTest(agent=name):
                self.assertTrue(agent_class.name)
                self.assertTrue(agent_class.role)
                self.assertTrue(agent_class.version)
                self.assertTrue(agent_class.effects)
                self.assertIn("type", agent_class.input_schema)
                self.assertIn("type", agent_class.output_schema)

    def test_describe_agents_returns_contracts(self):
        descriptions = describe_agents()
        self.assertEqual(len(descriptions), 6)
        for description in descriptions:
            self.assertIn("input_schema", description)
            self.assertIn("output_schema", description)
            self.assertIn("version", description)
            self.assertIn("effects", description)

    def test_analysis_declares_report_effects_others_none(self):
        descriptions = {item["name"]: item for item in describe_agents()}
        self.assertEqual(descriptions["analysis"]["effects"], ["artifacts:reports"])
        for name in ("evidence", "knowledge", "risk", "review", "injection"):
            self.assertEqual(descriptions[name]["effects"], ["none"], name)

    def test_strict_schemas_reject_undeclared_fields(self):
        # A task carrying an unknown field must fail the strict contract.
        errors = KnowledgeAgent.validate_input(
            {"candidates": [make_candidate()], "unexpected_field": True}
        )
        self.assertTrue(any("unexpected_field" in error for error in errors))

    def test_registry_rejects_duplicates_and_empty_names(self):
        with self.assertRaises(ValueError):
            register(AnalysisAgent)  # duplicate

        class Nameless(Agent):
            name = ""

        with self.assertRaises(ValueError):
            register(Nameless)

    def test_base_agent_run_raises(self):
        with self.assertRaises(NotImplementedError):
            Agent().run({})


class AnalysisAgentTest(unittest.TestCase):
    def test_run_produces_valid_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            init_repo(root)
            (root / "mod.py").write_text("def f(n):\n    if n > 300:\n        return n\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
            output = AnalysisAgent().run({"repo": str(root), "out_dir": str(root / "out")})
        self.assertEqual(AnalysisAgent.validate_output(output), [])
        self.assertGreaterEqual(output["modules_scanned"], 1)
        self.assertTrue(output["report_path"])

    def test_input_schema_rejects_missing_repo(self):
        errors = AnalysisAgent.validate_input({})
        self.assertTrue(errors)


class EvidenceAgentTest(unittest.TestCase):
    def test_enrich_from_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_path = root / "discovery.json"
            report_path.write_text(
                json.dumps({"candidates": [make_candidate()]}, ensure_ascii=False),
                encoding="utf-8",
            )
            output = EvidenceAgent().run({"discovery_report_path": str(report_path)})
        self.assertEqual(EvidenceAgent.validate_output(output), [])
        entry = output["candidates"][0]
        self.assertEqual(entry["id"], "cand_x_001")
        self.assertEqual(entry["evidence_count"], 2)
        self.assertEqual(entry["evidence_ids"], ["abc", "def"])
        self.assertIsNotNone(entry["confidence"])


class KnowledgeAgentTest(unittest.TestCase):
    def test_drafts_are_proposed_units_with_questions(self):
        output = KnowledgeAgent().run({"candidates": [make_candidate()]})
        self.assertEqual(KnowledgeAgent.validate_output(output), [])
        draft = output["drafts"][0]
        self.assertEqual(draft["status"], "proposed")
        self.assertEqual(draft["signal_kind"], "magic_number")
        self.assertTrue(draft["questions"])
        self.assertEqual(output["question_count"], len(draft["questions"]))

    def test_consumes_enriched_candidates_only(self):
        # Knowledge takes the evidence agent's candidates; a missing input
        # field is rejected by the strict schema.
        errors = KnowledgeAgent.validate_input({})
        self.assertTrue(errors)


class RiskAgentTest(unittest.TestCase):
    def run_risk(self, drafts):
        output = RiskAgent().run({"drafts": drafts})
        self.assertEqual(RiskAgent.validate_output(output), [])
        return {item["id"]: item for item in output["risks"]}

    def test_signal_risk_grading(self):
        risks = self.run_risk(
            [
                make_candidate(id="a", signal_kind="dependency_cycle"),
                make_candidate(id="b", signal_kind="long_function"),
                make_candidate(id="c", signal_kind="unknown"),
            ]
        )
        self.assertEqual(risks["a"]["signal_risk"], "HIGH")
        self.assertEqual(risks["b"]["signal_risk"], "LOW")
        self.assertEqual(risks["c"]["signal_risk"], "MEDIUM")

    def test_review_risk_reflects_evidence_quality(self):
        # Same signal kind, very different evidence quality -> different
        # review risk (the split Codex asked for).
        strong = make_candidate(id="strong", signal_kind="bridge_compat")
        weak = make_candidate(
            id="weak",
            signal_kind="bridge_compat",
            evidence=[],
            owner=None,
            confidence=None,
        )
        conflicting = make_candidate(
            id="conflicting",
            signal_kind="bridge_compat",
            evidence=[{"type": "commit", "id": "r", "role": "reverted"}],
        )
        risks = self.run_risk([strong, weak, conflicting])
        self.assertEqual(risks["strong"]["signal_risk"], "HIGH")
        self.assertEqual(risks["strong"]["review_risk"], "HIGH")  # falls back to signal
        self.assertEqual(risks["weak"]["review_risk"], "HIGH")    # >= 2 warnings
        self.assertEqual(risks["conflicting"]["review_risk"], "HIGH")  # hard conflict

        # A LOW-signal draft with hard conflicts shows the split clearly:
        # signal_risk stays LOW while review_risk jumps to HIGH.
        escalated = make_candidate(
            id="escalated",
            signal_kind="long_function",
            evidence=[{"type": "commit", "id": "r", "role": "reverted"}],
        )
        risks = self.run_risk([escalated])
        self.assertEqual(risks["escalated"]["signal_risk"], "LOW")
        self.assertEqual(risks["escalated"]["review_risk"], "HIGH")

    def test_conflicts_and_warnings_detected(self):
        risks = self.run_risk(
            [
                make_candidate(id="thin", evidence=[], owner=None),
                make_candidate(
                    id="revert",
                    evidence=[{"type": "commit", "id": "r", "role": "reverted"}],
                ),
                make_candidate(id="inferred", owner_inferred=True),
            ]
        )
        # Thin evidence / missing or inferred owners are warnings (ask the
        # owner), not hard conflicts.
        self.assertTrue(any("no traceable evidence" in w for w in risks["thin"]["warnings"]))
        self.assertTrue(any("no owner" in w for w in risks["thin"]["warnings"]))
        self.assertEqual(risks["thin"]["conflicts"], [])
        self.assertTrue(any("inferred" in w for w in risks["inferred"]["warnings"]))
        # Revert commits are hard conflicts (force human review).
        self.assertTrue(any("revert commit" in c for c in risks["revert"]["conflicts"]))

    def test_overlapping_scope_conflict(self):
        risks = self.run_risk(
            [
                make_candidate(id="a", scope={"files": ["x.py"], "symbols": []}),
                make_candidate(id="b", scope={"files": ["x.py"], "symbols": []}),
            ]
        )
        self.assertTrue(any("overlaps draft a" in c for c in risks["b"]["conflicts"]))


class ReviewAgentTest(unittest.TestCase):
    def run_review(self, drafts):
        # Route fixtures through the knowledge agent so drafts carry the
        # questions exactly as the real pipeline produces them.
        drafts = KnowledgeAgent().run({"candidates": drafts})["drafts"]
        risks = RiskAgent().run({"drafts": drafts})["risks"]
        output = ReviewAgent().run({"drafts": drafts, "risks": risks})
        self.assertEqual(ReviewAgent.validate_output(output), [])
        return {item["id"]: item for item in output["reviews"]}

    def test_ask_owner_when_thin_evidence(self):
        reviews = self.run_review([make_candidate(id="a", evidence=[], owner=None)])
        self.assertEqual(reviews["a"]["recommendation"], "ask_owner")
        # The draft's questions ride along so the human loop can start
        # directly from the run report.
        self.assertTrue(reviews["a"]["questions"])

    def test_confirm_carries_no_questions(self):
        reviews = self.run_review([make_candidate(id="a")])
        self.assertEqual(reviews["a"]["recommendation"], "confirm")
        self.assertNotIn("questions", reviews["a"])

    def test_human_review_on_conflict(self):
        reviews = self.run_review(
            [make_candidate(id="a", evidence=[{"type": "commit", "id": "r", "role": "reverted"}])]
        )
        self.assertEqual(reviews["a"]["recommendation"], "human_review")

    def test_human_review_on_low_confidence(self):
        reviews = self.run_review([make_candidate(id="a", confidence=0.2)])
        self.assertEqual(reviews["a"]["recommendation"], "human_review")

    def test_confirm_when_evidenced_and_confident(self):
        reviews = self.run_review([make_candidate(id="a")])
        self.assertEqual(reviews["a"]["recommendation"], "confirm")
        self.assertIn("evidence=2", reviews["a"]["summary"])


class InjectionAgentTest(unittest.TestCase):
    def test_preview_matched_and_unmatched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.json"
            registry.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "u1",
                                "title": "U",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["x.py"], "symbols": []},
                                "knowledge_delta": {"ops": [{"insert": "knowledge text"}]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = InjectionAgent().run(
                {
                    "repo": str(root),
                    "file_paths": ["x.py", "y.py"],
                    "registry_path": str(registry),
                }
            )
        self.assertEqual(InjectionAgent.validate_output(output), [])
        by_file = {item["file"]: item for item in output["previews"]}
        self.assertTrue(by_file["x.py"]["matched"])
        self.assertIn("knowledge text", by_file["x.py"]["text"])
        self.assertFalse(by_file["y.py"]["matched"])

    def test_preview_degrades_without_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = InjectionAgent().run({"repo": str(root), "file_paths": ["x.py"]})
        self.assertEqual(InjectionAgent.validate_output(output), [])
        self.assertFalse(output["previews"][0]["matched"])
        self.assertIn("暂无知识记录", output["previews"][0]["text"])

    def test_preview_finds_conventional_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            conventional = root / ".knowledge-ci" / "data" / "registry.json"
            conventional.parent.mkdir(parents=True)
            conventional.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "u1",
                                "title": "U",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["x.py"], "symbols": []},
                                "knowledge_delta": {"ops": [{"insert": "conventional"}]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = InjectionAgent().run({"repo": str(root), "file_paths": ["x.py"]})
        self.assertTrue(output["previews"][0]["matched"])


if __name__ == "__main__":
    unittest.main()
