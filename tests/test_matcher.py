import json
import tempfile
import unittest
from pathlib import Path

from src.registry.matcher import match_unit, normalize_path


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "registry.json"
        self.registry_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "units": [
                        {
                            "id": "payment_all",
                            "file_pattern": "src/payment/*.py",
                            "risk_level": "MEDIUM",
                        },
                        {
                            "id": "payment_retry",
                            "file_pattern": "src/payment/retry.py",
                            "risk_level": "HIGH",
                        },
                        {
                            "id": "refund_flow",
                            "file_pattern": "src/refund/**/*.py",
                            "risk_level": "HIGH",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_exact_match(self):
        self.assertEqual(
            match_unit("src/payment/retry.py", self.registry_path),
            "payment_retry",
        )

    def test_longest_match_wins(self):
        self.assertEqual(
            match_unit("src/payment/retry.py", self.registry_path),
            "payment_retry",
        )

    def test_glob_match(self):
        self.assertEqual(
            match_unit("src/payment/client.py", self.registry_path),
            "payment_all",
        )

    def test_recursive_glob_match(self):
        self.assertEqual(
            match_unit("src/refund/internal/flow.py", self.registry_path),
            "refund_flow",
        )

    def test_unmanaged_file_returns_none(self):
        self.assertIsNone(match_unit("src/utils/helper.py", self.registry_path))

    def test_windows_path_is_normalized(self):
        self.assertEqual(normalize_path(r"src\payment\retry.py"), "src/payment/retry.py")


if __name__ == "__main__":
    unittest.main()

