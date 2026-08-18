from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from git import NULL_TREE
from git import Repo

from src.registry.matcher import match_unit


CODE_SUFFIXES = {".py", ".js", ".ts", ".java"}
DOC_SUFFIXES = {".md", ".rst"}


@dataclass(frozen=True)
class DiffLine:
    kind: str
    old_lineno: int | None
    new_lineno: int | None
    text: str


def normalize_repo_path(file_path: str) -> str:
    normalized = str(PurePosixPath(file_path.replace("\\", "/")))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_code_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in CODE_SUFFIXES


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def _decode_blob(blob: Any | None) -> str:
    if blob is None:
        return ""
    # utf-8-sig strips a leading BOM that ast.parse would otherwise reject.
    return blob.data_stream.read().decode("utf-8-sig", errors="replace")


def _decode_patch(diff: Any) -> str:
    patch = diff.diff or b""
    if isinstance(patch, bytes):
        return patch.decode("utf-8", errors="replace")
    return str(patch)


def _status_for_diff(diff: Any) -> str:
    if diff.new_file:
        return "added"
    if diff.deleted_file:
        return "deleted"
    if diff.renamed_file:
        return "modified"
    return "modified"


def _path_for_diff(diff: Any) -> str:
    if diff.deleted_file:
        return normalize_repo_path(diff.a_path)
    return normalize_repo_path(diff.b_path or diff.a_path)


def parse_patch_lines(patch_text: str) -> list[DiffLine]:
    lines: list[DiffLine] = []
    old_lineno: int | None = None
    new_lineno: int | None = None

    for raw_line in patch_text.splitlines():
        header = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if header:
            old_lineno = int(header.group(1))
            new_lineno = int(header.group(2))
            continue

        if old_lineno is None or new_lineno is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            lines.append(DiffLine("added", None, new_lineno, raw_line[1:]))
            new_lineno += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            lines.append(DiffLine("removed", old_lineno, None, raw_line[1:]))
            old_lineno += 1
        elif raw_line.startswith(" "):
            old_lineno += 1
            new_lineno += 1
        elif raw_line.startswith("\\"):
            continue

    return lines


def _python_symbol_ranges(source: str) -> list[tuple[str, str, int, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    ranges: list[tuple[str, str, int, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ranges.append(("functions", node.name, node.lineno, getattr(node, "end_lineno", node.lineno)))
        elif isinstance(node, ast.ClassDef):
            ranges.append(("classes", node.name, node.lineno, getattr(node, "end_lineno", node.lineno)))

    return ranges


def _python_top_level_constants(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    constants: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]

        for target in targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                constants.add(target.id)

    return constants


def _symbols_covering_line(
    ranges: list[tuple[str, str, int, int]],
    line_number: int | None,
) -> tuple[list[str], list[str]]:
    if line_number is None:
        return [], []

    function_hits: list[tuple[int, str]] = []
    class_hits: list[tuple[int, str]] = []
    for kind, name, start, end in ranges:
        if start <= line_number <= end:
            span = end - start
            if kind == "functions":
                function_hits.append((span, name))
            else:
                class_hits.append((span, name))

    # The smallest range is the most specific enclosing symbol.
    functions = [name for _, name in sorted(function_hits)[:1]]
    classes = [name for _, name in sorted(class_hits)[:1]]
    return functions, classes


PY_CONSTANT_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=")
JS_FUNCTION_RE = re.compile(
    r"\b(?:function\s+([A-Za-z_$][\w$]*)|([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)"
)
JS_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
JS_CONSTANT_RE = re.compile(r"\b(?:const|let|var)\s+([A-Z][A-Z0-9_]*)\b|^\s*([A-Z][A-Z0-9_]*)\s*=")
JAVA_METHOD_RE = re.compile(
    r"\b(?:public|protected|private|static|final|synchronized|\s)+[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?"
)
JAVA_CLASS_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")
JAVA_CONSTANT_RE = re.compile(r"\b(?:static\s+final|final\s+static|final)\b[^;=]*\s+([A-Z][A-Z0-9_]*)\s*=")


def _extract_regex_symbols(lines: Iterable[str], suffix: str) -> dict[str, list[str]]:
    functions: list[str] = []
    classes: list[str] = []
    constants: list[str] = []

    for line in lines:
        if suffix in {".js", ".ts"}:
            for match in JS_FUNCTION_RE.finditer(line):
                functions.append(match.group(1) or match.group(2))
            classes.extend(match.group(1) for match in JS_CLASS_RE.finditer(line))
            for match in JS_CONSTANT_RE.finditer(line):
                constants.append(match.group(1) or match.group(2))
        elif suffix == ".java":
            functions.extend(match.group(1) for match in JAVA_METHOD_RE.finditer(line))
            classes.extend(match.group(1) for match in JAVA_CLASS_RE.finditer(line))
            constants.extend(match.group(1) for match in JAVA_CONSTANT_RE.finditer(line))
        else:
            for match in PY_CONSTANT_RE.finditer(line):
                constants.append(match.group(1))

    return {
        "functions": _unique_sorted(functions),
        "classes": _unique_sorted(classes),
        "constants": _unique_sorted(constants),
    }


def summarize_change(
    file_path: str,
    patch_text: str,
    old_source: str = "",
    new_source: str = "",
) -> dict[str, Any]:
    suffix = Path(file_path).suffix.lower()
    diff_lines = parse_patch_lines(patch_text)
    changed_text = [line.text for line in diff_lines if line.kind in {"added", "removed"}]

    functions: list[str] = []
    classes: list[str] = []
    constants: list[str] = []

    if suffix == ".py":
        old_ranges = _python_symbol_ranges(old_source)
        new_ranges = _python_symbol_ranges(new_source)

        for line in diff_lines:
            ranges = new_ranges if line.kind == "added" else old_ranges
            line_number = line.new_lineno if line.kind == "added" else line.old_lineno
            line_functions, line_classes = _symbols_covering_line(ranges, line_number)
            functions.extend(line_functions)
            classes.extend(line_classes)

        constants.extend(_python_top_level_constants(old_source) ^ _python_top_level_constants(new_source))

    regex_symbols = _extract_regex_symbols(changed_text, suffix)
    functions.extend(regex_symbols["functions"])
    classes.extend(regex_symbols["classes"])
    constants.extend(regex_symbols["constants"])

    return {
        "functions": _unique_sorted(functions),
        "classes": _unique_sorted(classes),
        "constants": _unique_sorted(constants),
        "diff_excerpt": _diff_excerpt(patch_text),
    }


def _diff_excerpt(patch_text: str, max_lines: int = 40) -> list[str]:
    excerpt: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith(("@@", "+", "-")) and not line.startswith(("+++", "---")):
            excerpt.append(line)
        if len(excerpt) >= max_lines:
            break
    return excerpt


def find_related_docs(
    project_path: str | Path,
    symbols: Iterable[str],
    max_docs_per_symbol: int = 10,
) -> list[dict[str, Any]]:
    root = Path(project_path)
    suggestions: list[dict[str, Any]] = []

    for symbol in _unique_sorted(symbols):
        docs: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
                continue
            relative_path = path.relative_to(root)
            if any(part.startswith(".") for part in relative_path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if symbol in text:
                docs.append(normalize_repo_path(str(relative_path)))
                if len(docs) >= max_docs_per_symbol:
                    break
        if docs:
            suggestions.append({"symbol": symbol, "docs": docs})

    return suggestions


def analyze_commit(
    commit_hash: str,
    project_path: str | Path,
    registry_path: str | Path,
    include_related_docs: bool = True,
) -> dict[str, Any]:
    repo = Repo(project_path)
    try:
        commit = repo.commit(commit_hash)
        if commit.parents:
            diffs = commit.parents[0].diff(commit, create_patch=True)
        else:
            diffs = commit.diff(NULL_TREE, create_patch=True)

        changed_files: list[dict[str, Any]] = []
        unmanaged_files: list[str] = []
        affected_units: set[str] = set()
        symbols: set[str] = set()

        for diff in diffs:
            file_path = _path_for_diff(diff)
            if not is_code_file(file_path):
                continue

            patch_text = _decode_patch(diff)
            old_source = _decode_blob(diff.a_blob)
            new_source = _decode_blob(diff.b_blob)
            unit_id = match_unit(file_path, registry_path)
            summary = summarize_change(file_path, patch_text, old_source, new_source)

            if unit_id is None:
                unmanaged_files.append(file_path)
            else:
                affected_units.add(unit_id)
                symbols.update(summary["functions"])
                symbols.update(summary["classes"])
                symbols.update(summary["constants"])

            changed_files.append(
                {
                    "path": file_path,
                    "status": _status_for_diff(diff),
                    "unit_id": unit_id,
                    "summary": summary,
                }
            )

        report: dict[str, Any] = {
            "commit": commit.hexsha,
            "commit_short": commit.hexsha[:8],
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "changed_files": changed_files,
            "affected_units": sorted(affected_units),
            "unmanaged_files": sorted(set(unmanaged_files)),
        }

        if include_related_docs:
            report["related_docs_suggestions"] = find_related_docs(project_path, symbols)

        return report
    finally:
        repo.close()


def write_report(report: dict[str, Any], reports_path: str | Path) -> Path:
    output_dir = Path(reports_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    commit_name = report.get("commit_short") or str(report["commit"])[:8]
    output_path = output_dir / f"impact_{commit_name}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
