from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any


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
) -> str | None:
    """Return the matched unit id, or None when the file is unmanaged."""
    registry = load_registry(registry_path)
    matches: list[tuple[int, str]] = []

    for unit in registry.get("units", []):
        pattern = unit.get("file_pattern")
        unit_id = unit.get("id")
        if not pattern or not unit_id:
            continue

        if _pattern_matches(pattern, file_path):
            matches.append((len(normalize_path(pattern)), unit_id))

    if not matches:
        return None

    return max(matches, key=lambda item: item[0])[1]


def match_unit_record(
    file_path: str,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any] | None:
    """Return the matched unit record, or None when the file is unmanaged."""
    unit_id = match_unit(file_path, registry_path)
    if unit_id is None:
        return None

    registry = load_registry(registry_path)
    return next((unit for unit in registry.get("units", []) if unit.get("id") == unit_id), None)

