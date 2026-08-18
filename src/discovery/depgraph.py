from __future__ import annotations

"""Whole-repository dependency graph built with the stdlib ``ast`` module.

v1 parses Python only. Modules are repo-relative dotted names (``requests.sessions``);
edges are ``import`` (module-level imports), ``use`` (cross-module symbol
references such as calls), and ``inherit`` (class bases defined elsewhere).
Files that fail to parse are skipped and reported, so one broken file never
blocks the whole run. The graph is cacheable per commit hash.
"""

import ast
import json
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "DEFAULT_IGNORED_DIRS",
    "ModuleGraph",
    "ModuleNode",
    "build_graph",
    "collect_python_files",
    "is_excluded",
]

#: Directories skipped when scanning for source files.
DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "venv",
        ".venv",
        "env",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "site-packages",
        "dist",
        "build",
    }
)


@dataclass
class ModuleNode:
    module: str
    path: str
    lines: int = 0
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)
    deps: set[str] = field(default_factory=set)
    dep_kinds: dict[str, list[str]] = field(default_factory=dict)

    @property
    def symbols(self) -> list[str]:
        return self.functions + self.classes + self.constants

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["deps"] = sorted(data["deps"])
        data["dep_kinds"] = {dep: sorted(kinds) for dep, kinds in data["dep_kinds"].items()}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModuleNode":
        return cls(
            module=data["module"],
            path=data["path"],
            lines=data.get("lines", 0),
            functions=data.get("functions", []),
            classes=data.get("classes", []),
            constants=data.get("constants", []),
            deps=set(data.get("deps", [])),
            dep_kinds={dep: list(kinds) for dep, kinds in data.get("dep_kinds", {}).items()},
        )


class ModuleGraph:
    """Nodes plus derived views: dependents, degree centrality, cycles."""

    def __init__(self, nodes: dict[str, ModuleNode], parse_errors: list[dict[str, str]] | None = None) -> None:
        self.nodes = nodes
        self.parse_errors = parse_errors or []
        self._dependents: dict[str, set[str]] | None = None

    # -- construction -------------------------------------------------------

    def add_edge(self, source: str, target: str, kind: str) -> None:
        if source == target or target not in self.nodes:
            return
        node = self.nodes[source]
        if target not in node.deps:
            node.deps.add(target)
            node.dep_kinds[target] = [kind]
        elif kind not in node.dep_kinds[target]:
            node.dep_kinds[target].append(kind)

    # -- derived views ------------------------------------------------------

    def dependents(self, module: str) -> set[str]:
        if self._dependents is None:
            reverse: dict[str, set[str]] = {name: set() for name in self.nodes}
            for name, node in self.nodes.items():
                for dep in node.deps:
                    reverse[dep].add(name)
            self._dependents = reverse
        return self._dependents.get(module, set())

    def edge_kinds(self, source: str, target: str) -> list[str]:
        return self.nodes[source].dep_kinds.get(target, [])

    def degree(self, module: str) -> int:
        return len(self.nodes[module].deps) + len(self.dependents(module))

    def degree_centrality(self, module: str) -> float:
        size = len(self.nodes)
        if size <= 1:
            return 0.0
        return self.degree(module) / (size - 1)

    def cross_layer_edges(self, module: str) -> int:
        """Edges whose other end lives in a different top-level directory."""
        top = module.split(".", 1)[0]
        count = 0
        for dep in self.nodes[module].deps:
            if dep.split(".", 1)[0] != top:
                count += 1
        for dependent in self.dependents(module):
            if dependent.split(".", 1)[0] != top:
                count += 1
        return count

    def cross_layer_impact(self, module: str) -> float:
        edges = len(self.nodes[module].deps) + len(self.dependents(module))
        if edges == 0:
            return 0.0
        return self.cross_layer_edges(module) / edges

    def import_cycles(self) -> list[list[str]]:
        """Strongly connected components (size > 1) over all edge kinds.

        Returns cycles sorted by size, descending; each cycle lists modules
        in SCC order.
        """
        index_counter = 0
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        cycles: list[list[str]] = []

        def strongconnect(vertex: str) -> None:
            nonlocal index_counter
            indices[vertex] = index_counter
            lowlinks[vertex] = index_counter
            index_counter += 1
            stack.append(vertex)
            on_stack.add(vertex)

            for dep in self.nodes[vertex].deps:
                if dep not in indices:
                    strongconnect(dep)
                    lowlinks[vertex] = min(lowlinks[vertex], lowlinks[dep])
                elif dep in on_stack:
                    lowlinks[vertex] = min(lowlinks[vertex], indices[dep])

            if lowlinks[vertex] == indices[vertex]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == vertex:
                        break
                if len(component) > 1:
                    cycles.append(component)

        for name in list(self.nodes):
            if name not in indices:
                strongconnect(name)
        return sorted(cycles, key=len, reverse=True)

    # -- serialization (commit-hash cache) -----------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {name: node.to_dict() for name, node in sorted(self.nodes.items())},
            "parse_errors": self.parse_errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModuleGraph":
        nodes = {name: ModuleNode.from_dict(raw) for name, raw in data.get("nodes", {}).items()}
        return cls(nodes, parse_errors=data.get("parse_errors", []))

    def save(self, cache_path: str | Path) -> None:
        Path(cache_path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, cache_path: str | Path) -> "ModuleGraph | None":
        path = Path(cache_path)
        if not path.is_file():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None


def _module_id_for_path(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def is_excluded(relative_posix: str, exclude_paths: Iterable[str]) -> bool:
    """Whether a repo-relative POSIX path matches any exclusion entry.

    Each entry is matched as a path prefix (``tests`` covers ``tests/...``)
    and as a glob (``tests/*.py``).
    """
    for raw_entry in exclude_paths:
        entry = str(raw_entry).replace("\\", "/").strip("/")
        if not entry:
            continue
        if (
            fnmatch(relative_posix, entry)
            or relative_posix == entry
            or relative_posix.startswith(entry + "/")
        ):
            return True
    return False


def collect_python_files(repo_root: Path, exclude_paths: Iterable[str] | None = None) -> list[Path]:
    """Python source files under ``repo_root``, skipping ignored/excluded paths."""
    files: list[Path] = []
    exclusions = list(exclude_paths or [])
    for path in sorted(repo_root.rglob("*.py")):
        relative = path.relative_to(repo_root)
        if any(part in DEFAULT_IGNORED_DIRS for part in relative.parts):
            continue
        if is_excluded(relative.as_posix(), exclusions):
            continue
        files.append(path)
    return files


def _parse_module(path: Path) -> ast.Module | None:
    try:
        # utf-8-sig strips a leading BOM, which the Python interpreter itself
        # accepts but ast.parse(source_string) does not.
        return ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"), filename=str(path))
    except (SyntaxError, UnicodeError, OSError):
        return None


def _defined_names(tree: ast.Module) -> set[str]:
    """Names defined at module level (defs, classes, assignments, imports)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            names.update(_top_level_defined_names(node))
    return names


def _top_level_defined_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(child, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            names.update(_top_level_defined_names(child))
    return names


def _imported_modules(tree: ast.Module, module_id: str, known_module_ids: set[str]) -> set[str]:
    """Resolve intra-repo import statements to known module ids."""
    resolved: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_dotted(alias.name, known_module_ids)
                if target and target != module_id:
                    resolved.add(target)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and node.level > 0:  # relative import: approximate by package
                base = module_id
            for alias in node.names:
                if alias.name == "*":
                    target = _resolve_dotted(base, known_module_ids)
                else:
                    target = _resolve_dotted(f"{base}.{alias.name}" if base else alias.name, known_module_ids) or _resolve_dotted(base, known_module_ids)
                if target and target != module_id:
                    resolved.add(target)
    return resolved


def _resolve_dotted(name: str, known_module_ids: set[str]) -> str | None:
    """Find the deepest known module matching ``name`` (or one of its prefixes)."""
    if name in known_module_ids:
        return name
    parts = name.split(".")
    for index in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:index])
        if candidate in known_module_ids:
            return candidate
    return None


def _referenced_names(tree: ast.Module) -> set[str]:
    """Names loaded (not locally defined) anywhere in the module."""
    references: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            references.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and isinstance(root.ctx, ast.Load):
                references.add(root.id)
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_root = base
                while isinstance(base_root, ast.Attribute):
                    base_root = base_root.value
                if isinstance(base_root, ast.Name):
                    references.add(base_root.id)
    return references


def build_graph(repo_root: str | Path, exclude_paths: Iterable[str] | None = None) -> ModuleGraph:
    """Build the dependency graph for a repository. See module docstring.

    ``exclude_paths`` drops matching files from the graph entirely (same
    semantics as ``is_excluded``).
    """
    root = Path(repo_root).resolve()
    graph = ModuleGraph({})
    if not root.is_dir():
        return graph

    files = collect_python_files(root, exclude_paths)
    trees: dict[str, ast.Module] = {}
    parse_errors: list[dict[str, str]] = []

    # Pass 1: module ids, symbol definitions, parse errors.
    for path in files:
        tree = _parse_module(path)
        if tree is None:
            relative = path.relative_to(root).as_posix()
            parse_errors.append({"path": relative, "error": "SyntaxError"})
            continue
        module_id = _module_id_for_path(path.relative_to(root))
        trees[module_id] = tree
        node = ModuleNode(
            module=module_id,
            path=path.relative_to(root).as_posix(),
            lines=sum(1 for _ in path.read_text(encoding="utf-8", errors="replace").splitlines()),
        )
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.functions.append(item.name)
            elif isinstance(item, ast.ClassDef):
                node.classes.append(item.name)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        node.constants.append(target.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id.isupper():
                node.constants.append(item.target.id)
        graph.nodes[module_id] = node

    # Symbol index for cross-module resolution (first defining module wins).
    symbol_index: dict[str, list[str]] = {}
    known_module_ids: set[str] = set(trees.keys())
    for module_id, tree in trees.items():
        for symbol in graph.nodes[module_id].symbols:
            symbol_index.setdefault(symbol, []).append(module_id)

    # Pass 2: edges.
    for module_id, tree in trees.items():
        for target in _imported_modules(tree, module_id, known_module_ids):
            graph.add_edge(module_id, target, "import")

        local_names = _defined_names(tree)
        for reference in sorted(_referenced_names(tree) - local_names):
            for target in symbol_index.get(reference, []):
                if target != module_id:
                    graph.add_edge(module_id, target, "use")

        for item in tree.body:
            if isinstance(item, ast.ClassDef):
                for base in item.bases:
                    root_name = base
                    while isinstance(root_name, ast.Attribute):
                        root_name = root_name.value
                    if isinstance(root_name, ast.Name):
                        for target in symbol_index.get(root_name.id, []):
                            if target != module_id:
                                graph.add_edge(module_id, target, "inherit")

    graph.parse_errors = parse_errors
    return graph
