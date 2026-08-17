from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from src.registry.schema import unit_patterns, unit_symbols


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "registry.json"


def normalize_path(file_path: str) -> str:
    """Normalize platform-specific paths to POSIX-style relative paths."""
    return str(PurePosixPath(file_path.replace("\\", "/"))).lstrip("./")


def load_registry(registry_path: str | Path = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    path = Path(registry_path)
    with path.open("r", encoding="utf-8") as registry_file:
        return json.load(registry_file)


def _pattern_matches(pattern: str, file_path: str) -> bool:
    normalized_pattern = normalize_path(pattern)
    normalized_path = normalize_path(file_path)

    if fnmatch(normalized_path, normalized_pattern):
        return True

    # Python's fnmatch does not treat ** as a recursive directory wildcard in
    # every useful case, so pathlib matching is used as a second pass.
    return PurePosixPath(normalized_path).match(normalized_pattern)


def match_unit(
    file_path: str,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    symbols: Iterable[str] | None = None,
) -> str | None:
    """Return the matched unit id, or None when the file is unmanaged.

    File patterns (v2 ``scope.files``, falling back to legacy ``file_pattern``)
    take priority and the longest pattern wins. When ``symbols`` is provided
    and no file pattern matches, units whose ``scope.symbols`` intersect the
    given symbols are used as a symbol-level fallback.
    """
    registry = load_registry(registry_path)
    symbol_set = {str(symbol) for symbol in symbols} if symbols is not None else None
    matches: list[tuple[int, str]] = []

    for unit in registry.get("units", []):
        unit_id = unit.get("id")
        if not unit_id:
            continue

        pattern_matched = False
        for pattern in unit_patterns(unit):
            if not pattern:
                continue
            if _pattern_matches(pattern, file_path):
                matches.append((len(normalize_path(pattern)), unit_id))
                pattern_matched = True
                break

        if not pattern_matched and symbol_set is not None and symbol_set.intersection(unit_symbols(unit)):
            # Symbol-level matches rank below any file-pattern match.
            matches.append((0, unit_id))

    if not matches:
        return None

    return max(matches, key=lambda item: item[0])[1]


def match_unit_record(
    file_path: str,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    symbols: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Return the matched unit record, or None when the file is unmanaged."""
    unit_id = match_unit(file_path, registry_path, symbols=symbols)
    if unit_id is None:
        return None

    registry = load_registry(registry_path)
    return next((unit for unit in registry.get("units", []) if unit.get("id") == unit_id), None)
