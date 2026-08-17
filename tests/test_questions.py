import json
import tempfile
import unittest
from pathlib import Path

from src.evidence.questions import (
    INSUFFICIENCY_QUESTIONS,
    QUESTION_TEMPLATES,
    QuestionDocError,
    add_answer,
    build_candidate_questions,
    generate_questions,
    save_questions_document,
    write_questions,
)


def make_candidate(**overrides):
    candidate = {
        "id": "cand_mod_001",
        "signal_kind": "magic_number",
        "title": "魔法数字/硬编码阈值 / magic number / hardcoded threshold: mod（待人工确认 / pending review）",
        "summary": "magic number 300 used in a condition",
        "rationale": "inferred",
        "scope": {"files": ["mod.py"], "symbols": []},
        "evidence": [{"type": "commit", "id": "abc", "short_id": "abc12345"}],
        "confidence": 0.3,
        "owner": "A <a@example.com>",
        "owner_inferred": True,
        "status": "proposed",
        "knowledge_delta": {"ops": [{"insert": "x"}]},
        "last_verified": None,
        "code_hash": "",
        "version": 1,
    }
    candidate.update(overrides)
    return candidate


class QuestionsTest(unittest.TestCase):
    def test_kind_questions_plus_insufficiency_when_thin_evidence(self):
        questions = build_candidate_questions("magic_number", evidence=[{"type": "commit"}])
        self.assertEqual(len(questions), len(INSUFFICIENCY_QUESTIONS) + len(QUESTION_TEMPLATES["magic_number"]))
        self.assertTrue(all({"zh", "en"} <= set(item) for item in questions))

    def test_no_insufficiency_questions_when_evidenced(self):
        evidence = [{"type": "commit", "id": "a"}, {"type": "incident", "id": "b"}]
        questions = build_candidate_questions("magic_number", evidence=evidence)
        self.assertEqual(len(questions), len(QUESTION_TEMPLATES["magic_number"]))

    def test_unknown_kind_yields_only_generic_questions(self):
        questions = build_candidate_questions("unknown_kind", evidence=None)
        self.assertEqual(len(questions), len(INSUFFICIENCY_QUESTIONS))

    def test_generate_questions_entry_shape(self):
        entries = generate_questions([make_candidate()])
        self.assertEqual(entries[0]["candidate_id"], "cand_mod_001")
        self.assertEqual(entries[0]["module"], "mod.py")
        self.assertTrue(entries[0]["insufficient"])
        self.assertEqual(entries[0]["answers"], [])

    def test_signal_kind_falls_back_to_title(self):
        candidate = make_candidate()
        candidate.pop("signal_kind")
        entries = generate_questions([candidate])
        self.assertGreater(len(entries[0]["items"]), 0)

    def test_write_questions_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            document, output_path = write_questions([make_candidate()], Path(temp) / "repo", Path(temp))
            self.assertTrue(output_path.name.startswith("questions_"))
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["questions"][0]["candidate_id"], "cand_mod_001")
            self.assertIn("repo", payload)

    def test_add_answer_appends_and_unknown_id_raises(self):
        document = {
            "generated_at": "2026-08-17T00:00:00Z",
            "repo": "repo",
            "questions": generate_questions([make_candidate()]),
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "q.json"
            updated = add_answer(document, "cand_mod_001", "业务阈值，来源见 SPEC-1。", owner="pay-team")
            save_questions_document(updated, path)
            reloaded = json.loads(path.read_text(encoding="utf-8"))
        answer_record = reloaded["questions"][0]["answers"][0]
        self.assertEqual(answer_record["answer"], "业务阈值，来源见 SPEC-1。")
        self.assertEqual(answer_record["owner"], "pay-team")
        self.assertIn("answered_at", answer_record)
        with self.assertRaises(QuestionDocError):
            add_answer(document, "missing_candidate", "x")


if __name__ == "__main__":
    unittest.main()
