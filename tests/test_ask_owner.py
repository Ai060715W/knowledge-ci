import argparse
import json
import tempfile
import unittest
from pathlib import Path

from src.cli.ask_owner import run


def make_args(**overrides):
    defaults = {
        "action": "questions",
        "report": None,
        "questions": None,
        "out": None,
        "candidate": None,
        "answer": None,
        "owner": None,
        "confirm": False,
        "registry": None,
        "config": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def write_report(root: Path) -> Path:
    report = {
        "generated_at": "2026-08-17T00:00:00Z",
        "repo": str(root),
        "candidates": [
            {
                "id": "cand_mod_001",
                "signal_kind": "magic_number",
                "title": "魔法数字/硬编码阈值 / magic number / hardcoded threshold: mod（待人工确认 / pending review）",
                "summary": "magic number 300 used in a condition",
                "rationale": "（推断，待人工确认 / inferred, pending human confirmation）证据提交：abc12345",
                "scope": {"files": ["mod.py"], "symbols": []},
                "evidence": [
                    {"type": "commit", "id": "abc12345", "short_id": "abc12345", "subject": "init", "author": "a@example.com", "role": "introduced"}
                ],
                "confidence": 0.3,
                "owner": "A <a@example.com>",
                "owner_inferred": True,
                "reviewer": None,
                "status": "proposed",
                "knowledge_delta": {"ops": [{"insert": "magic number 300 used in a condition"}]},
                "related_docs": [],
                "last_verified": None,
                "code_hash": "abc12345",
                "version": 1,
                "questions": [],
            }
        ],
    }
    path = root / "discovery_demo.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def write_empty_registry(root: Path) -> Path:
    path = root / "registry.json"
    path.write_text(
        json.dumps({"version": 2, "last_updated": "", "units": []}),
        encoding="utf-8",
    )
    return path


class AskOwnerQuestionsTest(unittest.TestCase):
    def test_questions_action_writes_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = write_report(root)
            out = root / "out"
            code = run(make_args(action="questions", report=str(report), out=str(out)))
            files = list(out.glob("questions_*.json"))
            self.assertEqual(code, 0)
            self.assertEqual(len(files), 1)
            document = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(document["questions"][0]["candidate_id"], "cand_mod_001")
            self.assertTrue(document["questions"][0]["insufficient"])

    def test_questions_action_missing_report(self):
        self.assertEqual(run(make_args(action="questions")), 1)

    def test_questions_action_no_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "empty.json"
            report.write_text(
                json.dumps({"generated_at": "", "repo": str(root), "candidates": []}),
                encoding="utf-8",
            )
            self.assertEqual(run(make_args(action="questions", report=str(report))), 0)


class AskOwnerAnswerTest(unittest.TestCase):
    def _prepare(self, temp: str):
        root = Path(temp)
        report = write_report(root)
        out = root / "out"
        run(make_args(action="questions", report=str(report), out=str(out)))
        questions_file = next(out.glob("questions_*.json"))
        registry = write_empty_registry(root)
        return root, report, questions_file, registry

    def test_answer_without_confirm_updates_document_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _, questions_file, registry = self._prepare(temp)
            code = run(
                make_args(
                    action="answer",
                    questions=str(questions_file),
                    candidate="cand_mod_001",
                    answer="300 是协议上限，见 SPEC-1。",
                )
            )
            document = json.loads(questions_file.read_text(encoding="utf-8"))
            stored = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(len(document["questions"][0]["answers"]), 1)
        self.assertEqual(stored["units"], [])

    def test_answer_confirm_lands_candidate_as_under_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root, report, questions_file, registry = self._prepare(temp)
            code = run(
                make_args(
                    action="answer",
                    questions=str(questions_file),
                    report=str(report),
                    candidate="cand_mod_001",
                    answer="300 是协议上限，见 SPEC-1。",
                    owner="payment-team",
                    confirm=True,
                    registry=str(registry),
                )
            )
            stored = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        unit = stored["units"][0]
        self.assertEqual(unit["id"], "cand_mod_001")
        self.assertEqual(unit["status"], "under_review")
        self.assertFalse(unit["owner_inferred"])
        self.assertEqual(unit["owner"], "payment-team")
        self.assertTrue(
            any(item["type"] == "human_answer" for item in unit["evidence"])
        )
        # Confidence must grow once the human answer is part of the chain.
        self.assertGreater(unit["confidence"], 0.3)

    def test_answer_confirm_without_registry_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root, report, questions_file, _ = self._prepare(temp)
            code = run(
                make_args(
                    action="answer",
                    questions=str(questions_file),
                    report=str(report),
                    candidate="cand_mod_001",
                    answer="x",
                    confirm=True,
                )
            )
        self.assertEqual(code, 1)

    def test_answer_confirm_unknown_candidate_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root, report, questions_file, registry = self._prepare(temp)
            code = run(
                make_args(
                    action="answer",
                    questions=str(questions_file),
                    report=str(report),
                    candidate="cand_unknown_999",
                    answer="x",
                    confirm=True,
                    registry=str(registry),
                )
            )
        self.assertEqual(code, 1)

    def test_answer_unknown_question_candidate_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _, questions_file, _ = self._prepare(temp)
            code = run(
                make_args(
                    action="answer",
                    questions=str(questions_file),
                    candidate="cand_missing",
                    answer="x",
                )
            )
        self.assertEqual(code, 1)

    def test_answer_missing_argument_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _, questions_file, _ = self._prepare(temp)
            self.assertEqual(
                run(make_args(action="answer", questions=str(questions_file), candidate="c")),
                1,
            )

    def test_answer_confirm_updates_existing_unit_keeping_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root, report, questions_file, registry = self._prepare(temp)
            existing = json.loads(registry.read_text(encoding="utf-8"))
            existing["units"].append(
                {
                    "id": "cand_mod_001",
                    "title": "T",
                    "status": "active",
                    "version": 1,
                    "scope": {"files": ["mod.py"], "symbols": []},
                    "evidence": [],
                    "knowledge_delta": {"ops": []},
                }
            )
            registry.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            code = run(
                make_args(
                    action="answer",
                    questions=str(questions_file),
                    report=str(report),
                    candidate="cand_mod_001",
                    answer="补充说明",
                    confirm=True,
                    registry=str(registry),
                )
            )
            stored = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(stored["units"][0]["status"], "active")


if __name__ == "__main__":
    unittest.main()
