import unittest

from src.evidence.confidence import (
    DEFAULT_CONFIDENCE_WEIGHTS,
    compute_confidence,
    is_sufficiently_evidenced,
)


def evidence(*types):
    return [{"type": item, "id": f"{item}-1"} for item in types]


class ConfidenceTest(unittest.TestCase):
    def test_no_evidence_is_unknown(self):
        self.assertIsNone(compute_confidence(None))
        self.assertIsNone(compute_confidence([]))

    def test_single_commit(self):
        self.assertEqual(compute_confidence(evidence("commit")), 0.3)

    def test_design_document_example(self):
        # commit + incident + human_answer -> 1 - (0.7 * 0.4 * 0.1) = 0.972
        self.assertEqual(compute_confidence(evidence("commit", "incident", "human_answer")), 0.97)

    def test_duplicate_types_count_once(self):
        self.assertEqual(compute_confidence(evidence("commit", "commit", "commit")), 0.3)

    def test_unknown_types_ignored(self):
        self.assertEqual(compute_confidence(evidence("commit", "blog_post")), 0.3)

    def test_human_answer_dominates(self):
        self.assertGreater(
            compute_confidence(evidence("commit", "human_answer")),
            compute_confidence(evidence("commit")),
        )

    def test_settings_override_weights(self):
        settings = {"discovery": {"confidence_weights": {"human_answer": 1.0}}}
        self.assertEqual(compute_confidence(evidence("human_answer"), settings), 1.0)

    def test_invalid_settings_values_ignored(self):
        settings = {"discovery": {"confidence_weights": {"commit": 5.0, "code": "x"}}}
        self.assertEqual(compute_confidence(evidence("commit"), settings), 0.3)

    def test_result_bounded(self):
        for types in (["code"], ["commit"], ["mr"], ["issue"], ["incident"], ["human_answer"]):
            with self.subTest(types=types):
                value = compute_confidence(evidence(*types))
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_sufficiency_rule(self):
        self.assertFalse(is_sufficiently_evidenced([]))
        self.assertFalse(is_sufficiently_evidenced(evidence("commit")))
        self.assertTrue(is_sufficiently_evidenced(evidence("commit", "commit")))

    def test_default_weights_cover_schema_types(self):
        from src.registry.schema_spec import UNIT_V2_SCHEMA

        schema_types = UNIT_V2_SCHEMA["properties"]["evidence"]["items"]["properties"]["type"]["enum"]
        self.assertEqual(set(schema_types), set(DEFAULT_CONFIDENCE_WEIGHTS))


if __name__ == "__main__":
    unittest.main()
