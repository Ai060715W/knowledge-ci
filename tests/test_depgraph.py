import tempfile
import unittest
from pathlib import Path

from src.discovery.depgraph import ModuleGraph, build_graph


def write_py(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class DepGraphTest(unittest.TestCase):
    def test_import_edges_resolve_intra_repo_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "pkg/__init__.py", "")
            write_py(root, "pkg/core.py", "import pkg.utils\nfrom pkg import helpers\n")
            write_py(root, "pkg/utils.py", "def helper():\n    return 1\n")
            write_py(root, "pkg/helpers.py", "def other():\n    return 2\n")
            graph = build_graph(root)
        self.assertIn("pkg.utils", graph.nodes["pkg.core"].deps)
        self.assertIn("pkg.helpers", graph.nodes["pkg.core"].deps)
        self.assertIn("import", graph.edge_kinds("pkg.core", "pkg.utils"))

    def test_use_edges_for_cross_module_symbols(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "a/defs.py", "class Engine:\n    pass\n")
            # Star import does not bind the symbol locally, so the reference
            # to Engine resolves through the symbol index -> a "use" edge.
            write_py(root, "b/user.py", "from a.defs import *\n\ndef run():\n    return Engine()\n")
            graph = build_graph(root)
        kinds = graph.edge_kinds("b.user", "a.defs")
        self.assertIn("import", kinds)
        self.assertIn("use", kinds)

    def test_inherit_edges_for_class_bases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "base.py", "class Base:\n    pass\n")
            write_py(root, "child.py", "from base import Base\n\nclass Child(Base):\n    pass\n")
            graph = build_graph(root)
        self.assertIn("inherit", graph.edge_kinds("child", "base"))

    def test_syntax_errors_are_skipped_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "good.py", "X = 1\n")
            write_py(root, "broken.py", "def broken(:\n")
            graph = build_graph(root)
        self.assertIn("good", graph.nodes)
        self.assertNotIn("broken", graph.nodes)
        self.assertTrue(any("broken.py" in error["path"] for error in graph.parse_errors))

    def test_bom_encoded_file_is_parsed(self):
        # A UTF-8 BOM is legal for the Python interpreter but ast.parse on a
        # string rejects it; discovery must not skip such files.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bom.py").write_text("X = 1\n", encoding="utf-8-sig")
            graph = build_graph(root)
        self.assertIn("bom", graph.nodes)
        self.assertEqual(graph.parse_errors, [])

    def test_cycle_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "a.py", "import b\n")
            write_py(root, "b.py", "import a\n")
            graph = build_graph(root)
        cycles = graph.import_cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]), {"a", "b"})

    def test_dependents_and_degree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "core.py", "X = 1\n")
            write_py(root, "one.py", "import core\n")
            write_py(root, "two.py", "import core\n")
            graph = build_graph(root)
        self.assertEqual(graph.dependents("core"), {"one", "two"})
        self.assertEqual(graph.degree("core"), 2)
        self.assertEqual(graph.degree_centrality("core"), 1.0)

    def test_cross_layer_impact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "svc/handler.py", "from models import User\n")
            write_py(root, "models/__init__.py", "class User:\n    pass\n")
            write_py(root, "svc/local.py", "import svc.handler\n")
            graph = build_graph(root)
        # handler -> models crosses layers; local -> handler does not.
        self.assertEqual(graph.cross_layer_edges("svc.handler"), 1)
        self.assertGreater(graph.cross_layer_impact("svc.handler"), 0)
        self.assertEqual(graph.cross_layer_edges("svc.local"), 0)

    def test_ignored_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "real.py", "X = 1\n")
            write_py(root, "venv/lib.py", "Y = 1\n")
            graph = build_graph(root)
        self.assertIn("real", graph.nodes)
        self.assertNotIn("venv.lib", graph.nodes)

    def test_exclude_paths_prefix_and_glob(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "keep.py", "X = 1\n")
            write_py(root, "tests/test_a.py", "Y = 1\n")
            write_py(root, "generated/gen_x.py", "Z = 1\n")
            graph = build_graph(root, exclude_paths=["tests", "generated/*"])
        self.assertIn("keep", graph.nodes)
        self.assertNotIn("tests.test_a", graph.nodes)
        self.assertNotIn("generated.gen_x", graph.nodes)

    def test_serialization_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "a.py", "import b\nX = 1\n")
            write_py(root, "b.py", "Y = 1\n")
            graph = build_graph(root)
            cache_path = Path(temp) / "graph.json"
            graph.save(cache_path)
            loaded = ModuleGraph.load(cache_path)
        self.assertEqual(set(loaded.nodes), {"a", "b"})
        self.assertEqual(loaded.edge_kinds("a", "b"), ["import"])
        self.assertIsNone(ModuleGraph.load(Path(temp) / "missing.json"))


if __name__ == "__main__":
    unittest.main()
