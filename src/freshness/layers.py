from __future__ import annotations

"""Freshness pipeline: the four-layer knowledge staleness funnel.

Every layer is a pure, independently testable function that returns a
structured decision record, so a report can explain exactly why a unit was
judged fresh or stale:

1. time        — git history anchor check (cheap, no parsing)
2. ast         — normalized AST comparison (filters formatting/comments/
                 docstrings, import reordering, unused-local renames)
3. impact      — does the semantic change touch the unit's scope, directly
                 or through nearby dependency edges?
4. llm         — final verdict (in ``src/freshness/llm.py``)

Design rule: layers are conservative — any uncertainty passes the unit to the
next layer instead of risking a false "still valid".
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from git import Repo

from src.discovery.depgraph import ModuleGraph

__all__ = [
    "AstVerdict",
    "ImpactVerdict",
    "TimeVerdict",
    "ast_semantic_filter",
    "impact_analysis",
    "layer_time",
]


@dataclass
class TimeVerdict:
    fresh: bool
    reason: str
    commits: list[dict[str, Any]] = field(default_factory=list)
    files_missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "time",
            "fresh": self.fresh,
            "reason": self.reason,
            "commits": self.commits,
            "files_missing": self.files_missing,
        }


@dataclass
class AstVerdict:
    semantic: bool
    reason: str
    per_file: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "ast",
            "semantic": self.semantic,
            "reason": self.reason,
            "per_file": self.per_file,
        }


@dataclass
class ImpactVerdict:
    in_scope: bool
    reason: str
    direct_hits: list[str] = field(default_factory=list)
    indirect_hits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "impact",
            "in_scope": self.in_scope,
            "reason": self.reason,
            "direct_hits": self.direct_hits,
            "indirect_hits": self.indirect_hits,
        }


# ---------------------------------------------------------------------------
# Layer 1: time
# ---------------------------------------------------------------------------


def _repo_or_none(repo_root: str | Path) -> Repo | None:
    try:
        return Repo(repo_root)
    except Exception:
        return None


def _commit_exists(repo: Repo, ref: str) -> bool:
    try:
        repo.commit(ref)
        return True
    except Exception:
        return False


def _collect_touching_commits(
    repo: Repo,
    paths: list[str],
    anchor: str,
) -> list[dict[str, Any]]:
    """Commits after ``anchor`` that touch any of ``paths`` (newest first)."""
    range_spec = f"{anchor}..HEAD"
    try:
        output = repo.git.log(
            "--format=%x00%H%x00%s",
            "--name-only",
            range_spec,
            "--",
            *paths,
        )
    except Exception:
        return []

    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in output.splitlines():
        if raw_line.startswith("\x00"):
            parts = raw_line.lstrip("\x00").split("\x00")
            if len(parts) >= 2 and current is not None:
                commits.append(current)
            current = {"id": parts[0], "subject": parts[1] if len(parts) > 1 else "", "paths": []}
            continue
        line = raw_line.strip()
        if line and current is not None:
            current["paths"].append(line)
    if current is not None:
        commits.append(current)
    return commits


def layer_time(
    repo_root: str | Path,
    unit: dict[str, Any],
    fallback_days: float = 30.0,
) -> TimeVerdict:
    """Layer 1: did anything touch the unit's files since its verification anchor?

    Anchor resolution order: ``code_hash``, then ``last_verified`` (ISO date),
    then ``--since <days>`` fallback. A unit whose files are all gone is
    reported with ``files_missing`` so the caller can short-circuit it.
    """
    root = Path(repo_root)
    files = [
        str(item)
        for item in (unit.get("scope", {}).get("files") or [unit.get("file_pattern")])
        if item
    ]

    missing = [path for path in files if not (root / path).is_file()]
    if files and len(missing) == len(files):
        return TimeVerdict(
            fresh=False,
            reason="all scope files are missing from the repository (deleted or renamed)",
            files_missing=missing,
        )

    repo = _repo_or_none(root)
    if repo is None:
        return TimeVerdict(fresh=False, reason="not a git repository; cannot time-filter")

    anchor: str | None = None
    anchor_kind = ""
    code_hash = str(unit.get("code_hash") or "").strip()
    if code_hash and _commit_exists(repo, code_hash):
        anchor = code_hash
        anchor_kind = "code_hash"
    last_verified = str(unit.get("last_verified") or "").strip()
    if anchor is None and last_verified:
        try:
            probe = repo.git.log("--format=%H", "--since", last_verified, "-1")
            if probe.strip():
                anchor = last_verified
                anchor_kind = "last_verified"
        except Exception:
            pass
    if anchor is None:
        anchor = f"{fallback_days} days ago"
        anchor_kind = "fallback_days"

    commits: list[dict[str, Any]] = []
    try:
        commits = _collect_touching_commits(repo, files, anchor)
    except Exception:
        commits = []
    finally:
        repo.close()

    if not commits:
        return TimeVerdict(
            fresh=True,
            reason=f"no commits touch the unit's files after its anchor ({anchor_kind}: {anchor})",
        )
    return TimeVerdict(
        fresh=False,
        reason=f"{len(commits)} commit(s) touch the unit's files after its anchor ({anchor_kind}: {anchor})",
        commits=commits[:20],
    )


# ---------------------------------------------------------------------------
# Layer 2: AST semantic filter
# ---------------------------------------------------------------------------


def _parse_or_none(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


class _ImportNormalizer(ast.NodeTransformer):
    """Sort module-level import statements so ordering changes are ignored."""

    def __init__(self) -> None:
        self._module_imports: list[ast.stmt] = []

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self._module_imports = []
        body: list[ast.stmt] = []
        for statement in node.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                self._module_imports.append(statement)
            else:
                body.append(statement)
        self._module_imports.sort(key=_import_sort_key)
        node.body = [*self._module_imports, *body]
        return self.generic_visit(node)


def _import_sort_key(statement: ast.stmt) -> tuple[str, str]:
    if isinstance(statement, ast.Import):
        names = ",".join(alias.name for alias in statement.names)
        return ("import", names)
    names = ",".join(alias.name for alias in statement.names)
    return ("from", f"{statement.module or ''}.{names}")


class _UnusedLocalNormalizer(ast.NodeTransformer):
    """Rename never-read local variables to per-scope placeholders.

    A local that is assigned but never loaded carries no observable meaning,
    so renaming it must not count as a semantic change. Function parameters
    and names declared global/nonlocal are excluded (their names are part of
    the observable interface).
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        loads: set[str] = set()
        stores: set[str] = set()
        excluded: set[str] = set()

        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Load):
                    loads.add(child.id)
                else:
                    stores.add(child.id)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                excluded.update(child.names)

        parameters = {arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg)}
        unused = sorted((stores - loads) - excluded - parameters)

        if not unused:
            return self.generic_visit(node)

        renames = {name: f"_unused_local_{index}" for index, name in enumerate(unused)}
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in renames:
                child.id = renames[child.id]
        return node


class _DocstringStripper(ast.NodeTransformer):
    """Remove docstring statements (first-statement string literals)."""

    def _strip(self, body: list[ast.stmt]) -> None:
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(
            first.value.value, str
        ):
            body.pop(0)

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self._strip(node.body)
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self._strip(node.body)
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        self._strip(node.body)
        return self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self._strip(node.body)
        return self.generic_visit(node)


def _normalized_dump(tree: ast.Module) -> str:
    tree = _DocstringStripper().visit(tree)
    tree = _ImportNormalizer().visit(tree)
    tree = _UnusedLocalNormalizer().visit(tree)
    return ast.dump(tree, include_attributes=False)


def ast_semantic_filter(
    old_sources: dict[str, str],
    new_sources: dict[str, str],
) -> AstVerdict:
    """Layer 2: does the (old -> new) content change carry real semantics?

    ``old_sources``/``new_sources`` map repo-relative paths to file content.
    Formatting, comments, docstrings, import ordering, and unused-local
    renames are normalized away; anything else — including files that fail to
    parse — is conservatively treated as semantic.
    """
    per_file: list[dict[str, Any]] = []
    semantic_any = False
    for path in sorted(set(old_sources) | set(new_sources)):
        old_source = old_sources.get(path, "")
        new_source = new_sources.get(path, "")
        old_tree = _parse_or_none(old_source)
        new_tree = _parse_or_none(new_source)
        if old_tree is None or new_tree is None:
            per_file.append({"path": path, "semantic": True, "reason": "parse failure (conservative)"})
            semantic_any = True
            continue
        semantic = _normalized_dump(old_tree) != _normalized_dump(new_tree)
        per_file.append(
            {
                "path": path,
                "semantic": semantic,
                "reason": "normalized AST differs" if semantic else "normalized AST identical",
            }
        )
        semantic_any = semantic_any or semantic

    if semantic_any:
        reason = "semantic change detected in at least one file"
    else:
        reason = "all changes are formatting/comments/import-order/unused-renames"
    return AstVerdict(semantic=semantic_any, reason=reason, per_file=per_file)


def _symbol_signatures(source: str) -> dict[str, str]:
    """Normalized per-symbol signatures for module-level definitions.

    The signature includes the node body, so a changed constant VALUE or an
    edited function counts as a changed symbol even when the name stays the
    same. Renames naturally show up as two changed entries.
    """
    tree = _parse_or_none(source)
    if tree is None:
        return {}
    signatures: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            signatures[node.name] = _normalized_dump(ast.Module(body=[node], type_ignores=[]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    signatures[target.id] = ast.dump(node.value, include_attributes=False)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.isupper():
            signatures[node.target.id] = ast.dump(node.value, include_attributes=False)
    return signatures


def changed_symbols(old_source: str, new_source: str) -> set[str]:
    """Symbols whose definition changed between two file versions."""
    old_signatures = _symbol_signatures(old_source)
    new_signatures = _symbol_signatures(new_source)
    names = set(old_signatures) | set(new_signatures)
    return {name for name in names if old_signatures.get(name) != new_signatures.get(name)}


# ---------------------------------------------------------------------------
# Layer 3: dependency impact
# ---------------------------------------------------------------------------


def _module_of_path(path: str, graph: ModuleGraph) -> str | None:
    for node in graph.nodes.values():
        if node.path == path:
            return node.module
    parts = Path(path).with_suffix("").parts
    dotted = ".".join(parts)
    if dotted in graph.nodes:
        return dotted
    return None


def _nearby_modules(module: str, graph: ModuleGraph, depth: int) -> set[str]:
    """Modules within ``depth`` edges of ``module`` (both directions)."""
    frontier = {module}
    seen: set[str] = set()
    for _ in range(depth):
        new_frontier: set[str] = set()
        for current in frontier - seen:
            seen.add(current)
            new_frontier.update(graph.nodes[current].deps)
            new_frontier.update(graph.dependents(current))
        frontier = new_frontier - seen
        if not frontier:
            break
    seen.update(frontier)
    return seen - {module}


def impact_analysis(
    unit: dict[str, Any],
    graph: ModuleGraph,
    semantic_files: dict[str, set[str]],
    depth: int = 2,
) -> ImpactVerdict:
    """Layer 3: does the semantic change touch the unit's knowledge scope?

    ``semantic_files`` maps repo-relative paths to their changed symbol sets.
    Direct hits: the unit's own scope files (symbols must intersect when the
    unit declares symbols). Indirect hits: modules within ``depth`` edges of
    the unit's modules in either direction (upstream assumptions, downstream
    interface usage).
    """
    scope_files = [
        str(item)
        for item in (unit.get("scope", {}).get("files") or [unit.get("file_pattern")])
        if item
    ]
    scope_symbols = {str(item) for item in (unit.get("scope", {}).get("symbols") or [])}
    changed_paths = set(semantic_files)

    direct_hits: list[str] = []
    for path in scope_files:
        if path not in changed_paths:
            continue
        changed = semantic_files.get(path, set())
        if scope_symbols and not (scope_symbols & changed):
            continue  # change is outside the symbols the knowledge cares about
        direct_hits.append(path)

    indirect_hits: list[dict[str, Any]] = []
    unit_modules = {module for path in scope_files if (module := _module_of_path(path, graph))}
    changed_modules = {
        module
        for path in changed_paths
        if (module := _module_of_path(path, graph))
    }

    for unit_module in unit_modules:
        nearby = _nearby_modules(unit_module, graph, depth)
        for changed_module in sorted(nearby & changed_modules):
            if changed_module == unit_module:
                continue  # already covered as a direct hit when the path matched
            indirect_hits.append(
                {
                    "unit_module": unit_module,
                    "changed_module": changed_module,
                    "direction": _edge_direction(unit_module, changed_module, graph),
                    "symbols": sorted(semantic_files.get(_path_of_module(changed_module, graph), set())),
                }
            )

    if direct_hits:
        return ImpactVerdict(
            in_scope=True,
            reason=f"semantic change hits the unit's own files: {', '.join(direct_hits)}",
            direct_hits=direct_hits,
            indirect_hits=indirect_hits,
        )
    if indirect_hits:
        return ImpactVerdict(
            in_scope=True,
            reason=f"semantic change in {len(indirect_hits)} nearby module(s) (depth <= {depth})",
            indirect_hits=indirect_hits,
        )
    if not unit_modules and not scope_files:
        return ImpactVerdict(
            in_scope=True,
            reason="unit has no file scope; cannot prove the change is out of scope (conservative)",
        )
    return ImpactVerdict(
        in_scope=False,
        reason="semantic change is outside the unit's scope and nearby dependency edges",
    )


def _edge_direction(unit_module: str, changed_module: str, graph: ModuleGraph) -> str:
    kinds = graph.edge_kinds(unit_module, changed_module)
    if kinds:
        return f"upstream ({'/'.join(kinds)})"
    kinds = graph.edge_kinds(changed_module, unit_module)
    if kinds:
        return f"downstream ({'/'.join(kinds)})"
    return "unknown"


def _path_of_module(module: str, graph: ModuleGraph) -> str:
    return graph.nodes[module].path if module in graph.nodes else module
