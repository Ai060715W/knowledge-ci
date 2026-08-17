import json
import tempfile
import unittest
from pathlib import Path

from src.registry.matcher import match_unit, match_unit_record, normalize_path


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

    def test_v2_scope_files_are_matched(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "v2_unit",
                                "title": "V2 unit",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["src/payment/retry.py"], "symbols": []},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(match_unit("src/payment/retry.py", registry_path), "v2_unit")
            self.assertIsNone(match_unit("src/utils/helper.py", registry_path))

    def test_symbol_fallback_matches_units_without_file_hit(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "refund_flow",
                                "title": "Refund",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["src/refund/flow.py"], "symbols": ["RefundStateMachine"]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            # File glob wins when it matches.
            self.assertEqual(match_unit("src/refund/flow.py", registry_path), "refund_flow")
            # Symbol-level fallback for a file the glob does not cover.
            self.assertEqual(
                match_unit("src/other/caller.py", registry_path, symbols=["RefundStateMachine"]),
                "refund_flow",
            )
            # No symbols passed -> no fallback.
            self.assertIsNone(match_unit("src/other/caller.py", registry_path))

    def test_file_match_outranks_symbol_match(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "symbol_only",
                                "title": "Symbol only",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": [], "symbols": ["PaymentRetry"]},
                            },
                            {
                                "id": "file_unit",
                                "title": "File unit",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["src/payment/retry.py"], "symbols": []},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                match_unit("src/payment/retry.py", registry_path, symbols=["PaymentRetry"]),
                "file_unit",
            )

    def test_match_unit_record_returns_v2_record(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": [
                            {
                                "id": "v2_unit",
                                "title": "V2 unit",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["src/payment/retry.py"], "symbols": []},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            record = match_unit_record("src/payment/retry.py", registry_path)
            self.assertEqual(record["id"], "v2_unit")
            self.assertEqual(record["title"], "V2 unit")


if __name__ == "__main__":
    unittest.main()

