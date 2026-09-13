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

#: Module-level names hinting at hand-rolled mutable state containers.
CACHE_NAME_PATTERN = re.compile(r"(cache|state|registry|pool|buffer|store)", re.IGNORECASE)

#: Synchronization primitive class names (threading/its aliases).
SYNC_PRIMITIVES = frozenset(
    {"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition", "Event"}
)

#: Comment markers suggesting deliberately kept logic.
KEEP_MARKERS = (
    "todo",
    "勿删",
    "不要删",
    "历史原因",
    "兼容旧版",
    "暂时保留",
    "保留勿动",
    "hack",
    "workaround",
)

#: Statement prefixes used to recognize a commented-out Python code block.
#: The expression is intentionally conservative: prose comments should not
#: become knowledge candidates merely because they span several lines.
_COMMENTED_CODE_RE = re.compile(
    r"(?:async\s+def\s+|def\s+|class\s+|import\s+|from\s+|return\b|raise\b|"
    r"if\s+|elif\s+|else:|for\s+|while\s+|try:|except\b|with\s+|"
    r"[A-Za-z_]\w*\s*(?:=|\())"
)

#: Minimum consecutive code-like comment lines to count as a kept block.
COMMENTED_BLOCK_MIN_LINES = 3

#: Signal kinds emitted by this module.
signal_kinds = (
    "magic_number",
    "global_instance",
    "bridge_compat",
    "long_function",
    "long_class",
    "dependency_cycle",
    "reverted_history",
    "exception_swallow",
    "special_cache",
    "redundant_branch",
    "kept_logic",
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


# ---------------------------------------------------------------------------
# Behavior anomaly detectors (design document: "行为异常信号")
# ---------------------------------------------------------------------------


def _exception_swallows(tree: ast.Module) -> list[tuple[str, int]]:
    """Bare excepts, ``except: pass``, and except-return-default degradation.

    These hide failures from callers and are classic "this looks wrong but is
    deliberate" knowledge candidates.
    """
    found: list[tuple[str, int]] = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        if handler.type is None:
            found.append(("bare except swallows every exception", handler.lineno))
            continue
        body = handler.body
        if len(body) == 1:
            statement = body[0]
            if isinstance(statement, ast.Pass):
                found.append(
                    (f"except {ast.unparse(handler.type)}: pass (exception swallowed)", handler.lineno)
                )
            elif isinstance(statement, ast.Return) and _is_default_return(statement.value):
                found.append(
                    (
                        f"except {ast.unparse(handler.type)}: return default "
                        f"(silent degradation)",
                        handler.lineno,
                    )
                )
    return found


def _is_default_return(value: ast.expr | None) -> bool:
    """Whether a return expression is a conventional local fallback value."""
    return value is None or isinstance(value, (ast.Constant, ast.Dict, ast.List, ast.Set, ast.Tuple))


def _assignment_target_and_value(node: ast.stmt) -> tuple[list[ast.expr], ast.expr] | None:
    """Return module-level assignment targets and value, including annotations."""
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target], node.value
    return None


def _called_name(call: ast.Call) -> str | None:
    """Final callee name for a simple or qualified call."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_mutable_container(value: ast.expr) -> bool:
    """Recognize literal and common constructor forms of mutable containers."""
    if isinstance(value, (ast.Dict, ast.List, ast.Set)):
        return True
    return isinstance(value, ast.Call) and _called_name(value) in {"dict", "list", "set", "defaultdict"}


def _special_caches(tree: ast.Module) -> list[tuple[str, int]]:
    """Module-level mutable state containers and hand-rolled sync primitives.

    A module-level dict/list/set whose name suggests cache/state/pool/store,
    or a module-level threading lock, is a strong "why not the shared
    component?" knowledge candidate.
    """
    found: list[tuple[str, int]] = []
    for node in tree.body:
        assignment = _assignment_target_and_value(node)
        if assignment is None:
            continue
        targets, value = assignment
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if _is_mutable_container(value) and CACHE_NAME_PATTERN.search(target.id):
                found.append((f"module-level mutable state container: {target.id}", node.lineno))
            elif isinstance(value, ast.Call):
                called = _called_name(value)
                if called in SYNC_PRIMITIVES:
                    found.append(
                        (f"hand-rolled synchronization primitive: {target.id} = {called}()", node.lineno)
                    )
    return found


def _body_signature(body: list[ast.stmt]) -> str:
    """Normalized structural signature of a statement list (docstring stripped)."""
    cleaned: list[ast.stmt] = []
    for statement in body:
        if not cleaned and isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(
            statement.value.value, str
        ):
            continue
        cleaned.append(statement)
    return ast.dump(ast.Module(body=cleaned, type_ignores=[]), include_attributes=False)


def _redundant_branches(tree: ast.Module) -> list[tuple[str, int]]:
    """Structurally identical branch bodies inside one if/elif chain.

    Duplicate bodies with different conditions are usually "cannot merge"
    logic whose reason only humans know.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        chain: list[tuple[str, str]] = [(ast.unparse(node.test), _body_signature(node.body))]
        current = node
        while current.orelse and len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
            child = current.orelse[0]
            chain.append((ast.unparse(child.test), _body_signature(child.body)))
            current = child
        seen: dict[str, str] = {}
        for condition, signature in chain:
            previous = seen.get(signature)
            if previous is not None and previous != condition:
                found.append(
                    (f"branch body duplicates the '{previous}' branch (cannot be merged?)", node.lineno)
                )
            else:
                seen.setdefault(signature, condition)
    return found


def _kept_logic(source_lines: list[str]) -> list[tuple[str, int]]:
    """Commented-out code blocks and explicit keep markers.

    "Seemingly redundant but repeatedly preserved" logic: large commented-out
    code blocks, or comments saying the code must stay for a reason.
    """
    found: list[tuple[str, int]] = []

    run: list[str] = []
    run_start = 0
    for index, line in enumerate(source_lines, start=1):
        comment = re.match(r"^\s*#\s*(.*)$", line)
        if comment and _COMMENTED_CODE_RE.match(comment.group(1)):
            if not run:
                run_start = index
            run.append(comment.group(1))
        else:
            if len(run) >= COMMENTED_BLOCK_MIN_LINES:
                found.append(
                    (f"commented-out code block ({len(run)} lines) kept in place", run_start)
                )
            run = []
    if len(run) >= COMMENTED_BLOCK_MIN_LINES:
        found.append((f"commented-out code block ({len(run)} lines) kept in place", run_start))

    for index, line in enumerate(source_lines, start=1):
        comment = re.match(r"^\s*#\s*(.*)$", line)
        if not comment:
            continue
        lowered = comment.group(1).lower()
        for marker in KEEP_MARKERS:
            if marker.lower() in lowered:
                found.append((f"keep marker '{marker}' found", index))
                break
    return found


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
            # utf-8-sig: BOM is legal on disk but ast.parse rejects it in a string.
            source = path.read_text(encoding="utf-8-sig", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeError, OSError):
            continue

        module_signals = signals[module_id]
        relative_path = path.relative_to(root).as_posix()
        for value, lineno in _condition_numbers(tree):
            module_signals.append(
                Signal(
                    kind="magic_number",
                    path=relative_path,
                    detail=f"magic number {value} used in a condition",
                    line=lineno,
                )
            )
        for detail, lineno in _global_instances(tree):
            module_signals.append(
                Signal(
                    kind="global_instance",
                    path=relative_path,
                    detail=detail,
                    line=lineno,
                )
            )
        for signal in _long_spans(tree, long_span_lines):
            signal.path = relative_path
            module_signals.append(signal)
        for detail, lineno in _exception_swallows(tree):
            module_signals.append(
                Signal(kind="exception_swallow", path=relative_path, detail=detail, line=lineno)
            )
        for detail, lineno in _special_caches(tree):
            module_signals.append(
                Signal(kind="special_cache", path=relative_path, detail=detail, line=lineno)
            )
        for detail, lineno in _redundant_branches(tree):
            module_signals.append(
                Signal(kind="redundant_branch", path=relative_path, detail=detail, line=lineno)
            )
        for detail, lineno in _kept_logic(source.splitlines()):
            module_signals.append(
                Signal(kind="kept_logic", path=relative_path, detail=detail, line=lineno)
            )

        basename = path.stem
        docstring = ast.get_docstring(tree) or ""
        if BRIDGE_NAME_PATTERN.search(basename) or any(
            keyword in docstring.lower() for keyword in BRIDGE_DOCSTRING_KEYWORDS
        ):
            module_signals.append(
                Signal(
                    kind="bridge_compat",
                    path=relative_path,
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
