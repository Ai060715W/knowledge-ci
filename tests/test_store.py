import json
import tempfile
import unittest
from pathlib import Path

from src.registry.schema import RegistryValidationError
from src.registry.store import RegistryStore, atomic_write_json


class AtomicWriteTest(unittest.TestCase):
    def test_atomic_write_creates_parents_and_trailing_newline(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nested" / "deep" / "registry.json"
            atomic_write_json(target, {"version": 2, "units": []})
            self.assertTrue(target.is_file())
            self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))

    def test_atomic_write_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "registry.json"
            atomic_write_json(target, {"version": 2, "units": []})
            leftovers = [p.name for p in Path(temp).iterdir() if p.name != "registry.json"]
            self.assertEqual(leftovers, [])


class RegistryStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "data").mkdir()
        self.registry_path = root / "data" / "registry.json"
        self.registry_path.write_text(
            json.dumps({"version": 2, "last_updated": "", "units": []}),
            encoding="utf-8",
        )
        self.store = RegistryStore(
            registry_path=self.registry_path,
            evidence_path=root / "data" / "evidence",
            metrics_path=root / "data" / "metrics",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load_round_trip(self):
        registry = {"version": 2, "last_updated": "2026-08-17", "units": []}
        self.store.save_registry(registry)
        loaded = self.store.load_registry()
        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded["units"], [])

    def test_save_validates_by_default(self):
        with self.assertRaises(RegistryValidationError):
            self.store.save_registry({"version": 2, "units": [{"id": "missing-fields"}]})

    def test_save_skips_validation_when_requested(self):
        self.store.save_registry({"version": 2, "units": [{"id": "legacy"}]}, validate=False)
        self.assertEqual(self.store.load_registry()["units"][0]["id"], "legacy")

    def test_upsert_inserts_then_replaces(self):
        unit_a = {
            "id": "u1",
            "title": "first",
            "status": "active",
            "version": 1,
            "scope": {"files": ["a.py"], "symbols": []},
        }
        unit_b = dict(unit_a, title="second")
        self.store.upsert_unit(unit_a)
        self.store.upsert_unit(unit_b)
        self.assertEqual(self.store.find_unit("u1")["title"], "second")
        self.assertEqual(len(self.store.load_registry()["units"]), 1)

    def test_remove_unit_returns_removed(self):
        unit = {"id": "u1", "title": "t", "status": "active", "version": 1}
        self.store.upsert_unit(unit)
        removed = self.store.remove_unit("u1")
        self.assertEqual(removed["id"], "u1")
        self.assertIsNone(self.store.find_unit("u1"))
        self.assertIsNone(self.store.remove_unit("u1"))

    def test_evidence_save_load_and_key_safety(self):
        self.store.save_evidence("unit-u1", {"type": "commit", "id": "abc"})
        loaded = self.store.load_evidence("unit-u1")
        self.assertEqual(loaded["id"], "abc")
        self.assertIsNone(self.store.load_evidence("missing"))
        with self.assertRaises(ValueError):
            self.store.save_evidence("../evil", {})

    def test_metrics_save_requires_config(self):
        store = RegistryStore(registry_path=self.registry_path)
        with self.assertRaises(RuntimeError):
            store.save_metrics("kpi", {})

    def test_metrics_round_trip(self):
        self.store.save_metrics("2026-08", {"coverage": 0.5})
        path = self.store.metrics_dir() / "2026-08.json"
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["coverage"], 0.5)


if __name__ == "__main__":
    unittest.main()
