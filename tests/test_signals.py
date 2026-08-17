import subprocess
import tempfile
import unittest
from pathlib import Path

from src.discovery.depgraph import build_graph
from src.discovery.signals import detect_signals


def write_py(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


class SignalsTest(unittest.TestCase):
    def test_magic_number_in_condition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "mod.py", "def check(n):\n    if n > 300:\n        return True\n    return False\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph)
        self.assertTrue(any(s.kind == "magic_number" for s in signals["mod"]))
        self.assertFalse(any(s.kind == "magic_number" and s.line == 1 for s in signals["mod"]))

    def test_common_numbers_not_flagged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "mod.py", "def check(n):\n    if n > 1:\n        return True\n    return False\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph)
        self.assertFalse(any(s.kind == "magic_number" for s in signals["mod"]))

    def test_global_instance_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "mod.py", "class Client:\n    pass\n\nclient = Client()\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph)
        matches = [s for s in signals["mod"] if s.kind == "global_instance"]
        self.assertEqual(len(matches), 1)
        self.assertIn("client = Client()", matches[0].detail)

    def test_bridge_compat_from_filename_and_docstring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "compat.py", '"""Backward compatibility layer."""\nX = 1\n')
            write_py(root, "shim_layer.py", "Y = 1\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph)
        self.assertTrue(any(s.kind == "bridge_compat" for s in signals["compat"]))
        self.assertTrue(any(s.kind == "bridge_compat" for s in signals["shim_layer"]))

    def test_long_function_and_class_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = "\n".join(f"    x{i} = {i}" for i in range(60))
            write_py(root, "mod.py", f"def long():\n{body}\n\nclass Big:\n{body}\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph, long_span_lines=50)
        kinds = {s.kind for s in signals["mod"]}
        self.assertIn("long_function", kinds)
        self.assertIn("long_class", kinds)

    def test_reverted_history_signal_from_stats(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "mod.py", "X = 1\n")
            init_repo(root)
            graph = build_graph(root)
            stats = {"mod": {"reverts": 2}}
            signals = detect_signals(root, graph, stats=stats)
        self.assertTrue(any(s.kind == "reverted_history" for s in signals["mod"]))

    def test_cycle_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "a.py", "import b\n")
            write_py(root, "b.py", "import a\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph)
        self.assertTrue(any(s.kind == "dependency_cycle" for s in signals["a"]))
        self.assertTrue(any(s.kind == "dependency_cycle" for s in signals["b"]))

    def test_clean_module_has_no_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_py(root, "mod.py", "def helper():\n    return 1\n")
            graph = build_graph(root)
            signals = detect_signals(root, graph)
        self.assertEqual(signals["mod"], [])


if __name__ == "__main__":
    unittest.main()
