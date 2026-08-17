from __future__ import annotations

"""Structural anomaly signals for hidden-knowledge discovery.

Each detector is a cheap, deterministic rule. Signals are hints that a piece
of code deserves a knowledge unit — they are never treated as facts on their
own; the downstream pipeline turns them into candidate drafts and questions.
"""

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.discovery.depgraph import ModuleGraph, collect_python_files

__all__ = [
    "BRIDGE_NAME_PATTERN",
    "BRIDGE_DOCSTRING_KEYWORDS",
    "COMMON_SAFE_NUMBERS",
    "DEFAULT_LONG_SPAN_LINES",
    "Signal",
    "detect_signals",
    "signal_kinds",
]

#: File names hinting at compatibility/bridge layers.
BRIDGE_NAME_PATTERN = re.compile(r"(compat|legacy|bridge|adapter|shim|deprecated|backport)", re.IGNORECASE)

#: Docstring phrases hinting that a module exists for historical reasons.
BRIDGE_DOCSTRING_KEYWORDS = (
    "backward compat",
    "backwards compat",
    "compatibility",
    "deprecated",
    "legacy support",
)

#: Numbers that are ubiquitous and therefore never flagged as magic.
COMMON_SAFE_NUMBERS: frozenset[int] = frozenset({0, 1, 2, -1})

#: Default line-span threshold for "long function/class" signals.
DEFAULT_LONG_SPAN_LINES = 80

#: Signal kinds emitted by this module.
signal_kinds = (
    "magic_number",
    "global_instance",
    "bridge_compat",
    "long_function",
    "long_class",
    "dependency_cycle",
    "reverted_history",
)


@dataclass
class Signal:
    kind: str
    path: str
    detail: str
    line: int | None = None
    symbols: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numeric_literals(node: ast.AST) -> list[tuple[float, int]]:
    """Numeric constants under ``node`` that are not common safe values."""
    found: list[tuple[float, int]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, (int, float)):
            value = child.value
            if isinstance(value, float) or value not in COMMON_SAFE_NUMBERS:
                lineno = getattr(child, "lineno", 0) or 0
                found.append((value, lineno))
    return found


def _condition_numbers(tree: ast.Module) -> list[tuple[float, int]]:
    """Magic numbers inside If/While conditions and comparisons."""
    found: list[tuple[float, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            found.extend(_numeric_literals(node.test))
        elif isinstance(node, ast.Compare):
            found.extend(_numeric_literals(node))
    return found


def _global_instances(tree: ast.Module) -> list[tuple[str, int]]:
    """Module-level ``name = ClassName(...)`` instantiations."""
    found: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, ast.Call):
                func = value.func
                while isinstance(func, ast.Attribute):
                    func = func.value
                if isinstance(func, ast.Name):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            found.append((f"{target.id} = {func.id}()", node.lineno))
                            break
    return found


def _long_spans(tree: ast.Module, threshold: int) -> list[Signal]:
    signals: list[Signal] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            if end - start + 1 >= threshold:
                kind = "long_class" if isinstance(node, ast.ClassDef) else "long_function"
                signals.append(
                    Signal(
                        kind=kind,
                        path="",
                        detail=f"{node.name} spans {end - start + 1} lines (threshold {threshold})",
                        line=start,
                        symbols=[node.name],
                    )
                )
    return signals


def detect_signals(
    repo_root: str | Path,
    graph: ModuleGraph,
    stats: dict[str, dict[str, Any]] | None = None,
    long_span_lines: int = DEFAULT_LONG_SPAN_LINES,
    exclude_paths: list[str] | None = None,
) -> dict[str, list[Signal]]:
    """Detect structural signals per module.

    ``stats`` (from scoring.collect_commit_stats) adds ``reverted_history``
    signals for modules with revert commits. Returns {module: [Signal]}.
    """
    root = Path(repo_root).resolve()
    signals: dict[str, list[Signal]] = {name: [] for name in graph.nodes}
    path_to_module = {node.path: name for name, node in graph.nodes.items()}

    for path in collect_python_files(root, exclude_paths):
        module_id = path_to_module.get(path.relative_to(root).as_posix())
        if module_id is None:
            continue  # unparsed file; the graph already reported it
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, UnicodeError, OSError):
            continue

        module_signals = signals[module_id]
        for value, lineno in _condition_numbers(tree):
            module_signals.append(
                Signal(
                    kind="magic_number",
                    path=path.relative_to(root).as_posix(),
                    detail=f"magic number {value} used in a condition",
                    line=lineno,
                )
            )
        for detail, lineno in _global_instances(tree):
            module_signals.append(
                Signal(
                    kind="global_instance",
                    path=path.relative_to(root).as_posix(),
                    detail=detail,
                    line=lineno,
                )
            )
        for signal in _long_spans(tree, long_span_lines):
            signal.path = path.relative_to(root).as_posix()
            module_signals.append(signal)

        basename = path.stem
        docstring = ast.get_docstring(tree) or ""
        if BRIDGE_NAME_PATTERN.search(basename) or any(
            keyword in docstring.lower() for keyword in BRIDGE_DOCSTRING_KEYWORDS
        ):
            module_signals.append(
                Signal(
                    kind="bridge_compat",
                    path=path.relative_to(root).as_posix(),
                    detail=f"compatibility/bridge layer hint (name or docstring)",
                    line=1,
                )
            )

    for cycle in graph.import_cycles():
        for member in cycle:
            signals.setdefault(member, []).append(
                Signal(
                    kind="dependency_cycle",
                    path=graph.nodes[member].path,
                    detail="import cycle: " + " -> ".join([*cycle, cycle[0]]),
                    line=None,
                    symbols=None,
                )
            )

    for module_id, entry in (stats or {}).items():
        if entry.get("reverts") and module_id in signals:
            signals[module_id].append(
                Signal(
                    kind="reverted_history",
                    path=graph.nodes[module_id].path,
                    detail=f"{entry['reverts']} revert commit(s) touched this module",
                    line=None,
                    symbols=None,
                )
            )

    return signals
