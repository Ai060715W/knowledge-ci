import argparse
import json
import tempfile
import unittest
from pathlib import Path

from src.cli.migrate import BACKUP_SUFFIX, run


def make_args(**overrides):
    defaults = {
        "registry": None,
        "config": None,
        "dry_run": False,
        "no_backup": False,
        "rollback": False,
        "force": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def write_v1_registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "units": [
                    {
                        "id": "payment_retry",
                        "name": "支付重试",
                        "file_pattern": "src/payment/retry.py",
                        "risk_level": "HIGH",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class MigrateCliTest(unittest.TestCase):
    def test_migrate_v1_to_v2(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            write_v1_registry(registry_path)
            self.assertEqual(run(make_args(registry=str(registry_path))), 0)
            migrated = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["version"], 2)
            unit = migrated["units"][0]
            self.assertEqual(unit["title"], "支付重试")
            self.assertEqual(unit["scope"]["files"], ["src/payment/retry.py"])
            self.assertEqual(unit["status"], "active")
            self.assertTrue((Path(temp) / ("registry.json" + BACKUP_SUFFIX)).is_file())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            write_v1_registry(registry_path)
            original = registry_path.read_text(encoding="utf-8")
            self.assertEqual(run(make_args(registry=str(registry_path), dry_run=True)), 0)
            self.assertEqual(registry_path.read_text(encoding="utf-8"), original)
            self.assertFalse((Path(temp) / ("registry.json" + BACKUP_SUFFIX)).exists())

    def test_already_v2_is_noop_without_force(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            write_v1_registry(registry_path)
            run(make_args(registry=str(registry_path)))
            self.assertEqual(run(make_args(registry=str(registry_path))), 0)
            migrated = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["version"], 2)

    def test_rollback_restores_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            write_v1_registry(registry_path)
            run(make_args(registry=str(registry_path)))
            run(make_args(registry=str(registry_path), rollback=True))
            restored = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["version"], 1)
            self.assertIn("file_pattern", restored["units"][0])

    def test_rollback_without_backup_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            write_v1_registry(registry_path)
            self.assertEqual(run(make_args(registry=str(registry_path), rollback=True)), 1)

    def test_missing_registry_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(run(make_args(registry=str(Path(temp) / "nope.json"))), 1)

    def test_no_backup_flag_skips_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            write_v1_registry(registry_path)
            self.assertEqual(run(make_args(registry=str(registry_path), no_backup=True)), 0)
            self.assertFalse((Path(temp) / ("registry.json" + BACKUP_SUFFIX)).exists())


if __name__ == "__main__":
    unittest.main()
