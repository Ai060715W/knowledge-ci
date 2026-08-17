from __future__ import annotations

"""Knowledge unit schema v2: validation, status state machine, and v1->v2 migration.

Schema v2 adds the fields from the original design document (title/summary/
rationale, symbol-level scope, evidence chain, confidence, owner/reviewer, and a
knowledge status state machine) while keeping every v1 field readable so
existing registries keep working without migration.
"""

import copy
from typing import Any

import jsonschema

from src.registry.schema_spec import REGISTRY_V2_SCHEMA, UNIT_V2_SCHEMA

__all__ = [
    "UNIT_STATUSES",
    "STATUS_TRANSITIONS",
    "RegistryValidationError",
    "transition_status",
    "unit_patterns",
    "unit_title",
    "unit_name",
    "unit_symbols",
    "is_injectable",
    "validate_unit",
    "validate_registry",
    "migrate_unit",
    "migrate_registry",
]


#: Allowed knowledge unit statuses.
UNIT_STATUSES: tuple[str, ...] = (
    "proposed",      # discovery candidate, not yet reviewed
    "under_review",  # inside the existing patch review pipeline
    "active",        # live knowledge, injected before edits
    "outdated",      # freshness check found it inconsistent with the code
    "retired",       # no longer valid, never injected
)

#: Legal transitions. Missing status (v1 registries) is treated as ``active``.
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": ("under_review", "retired"),
    "under_review": ("active", "retired"),
    "active": ("outdated", "retired"),
    "outdated": ("active", "retired"),
    "retired": (),
}


class RegistryValidationError(ValueError):
    """Raised when a registry or knowledge unit violates schema v2."""


def _current_status(unit: dict[str, Any]) -> str:
    status = unit.get("status")
    return status if status in UNIT_STATUSES else "active"


def transition_status(unit: dict[str, Any], new_status: str) -> dict[str, Any]:
    """Move a unit to ``new_status``, rejecting illegal transitions in place."""
    if new_status not in UNIT_STATUSES:
        raise RegistryValidationError(
            f"Unknown status {new_status!r}. Allowed: {', '.join(UNIT_STATUSES)}."
        )
    current = _current_status(unit)
    if new_status == current:
        unit["status"] = current
        return unit
    allowed = STATUS_TRANSITIONS[current]
    if new_status not in allowed:
        raise RegistryValidationError(
            f"Illegal status transition: {current} -> {new_status}. "
            f"Allowed from {current}: {', '.join(allowed) or 'none'}."
        )
    unit["status"] = new_status
    return unit


def unit_patterns(unit: dict[str, Any]) -> list[str]:
    """File glob patterns for a unit, v2 ``scope.files`` first, v1 fallback.

    Duplicates are removed and order is preserved.
    """
    scope_files = unit.get("scope", {}).get("files") if isinstance(unit.get("scope"), dict) else None
    candidates = list(scope_files or [])
    legacy = unit.get("file_pattern")
    if legacy:
        candidates.append(legacy)
    patterns: list[str] = []
    for pattern in candidates:
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return patterns


def unit_symbols(unit: dict[str, Any]) -> list[str]:
    """Symbol-level scope for a unit (empty for v1 units)."""
    scope = unit.get("scope")
    if not isinstance(scope, dict):
        return []
    return [symbol for symbol in (scope.get("symbols") or []) if symbol]


def unit_title(unit: dict[str, Any]) -> str:
    """Display title: v2 ``title`` first, then v1 ``name``, then ``id``."""
    return str(unit.get("title") or unit.get("name") or unit.get("id", ""))


def unit_name(unit: dict[str, Any]) -> str:
    """Legacy display-name accessor; same fallback chain as ``unit_title``."""
    return unit_title(unit)


def is_injectable(unit: dict[str, Any]) -> bool:
    """Only active knowledge (or legacy units without a status) is injected."""
    return _current_status(unit) == "active"


def validate_unit(unit: Any) -> list[str]:
    """Validate one unit against schema v2. Returns a list of error messages."""
    validator = jsonschema.Draft7Validator(UNIT_V2_SCHEMA)
    errors = [error.message for error in validator.iter_errors(unit)]
    return errors


def validate_registry(registry: Any) -> list[str]:
    """Validate a registry document. Returns a list of error messages.

    Registries whose top-level ``version`` is below 2 are legacy documents and
    only get a structural sanity check (units is a list of objects), so the
    existing pipeline keeps working on unmigrated data.
    """
    if not isinstance(registry, dict):
        return ["registry must be a JSON object"]
    if int(registry.get("version", 0)) >= 2:
        validator = jsonschema.Draft7Validator(REGISTRY_V2_SCHEMA)
        return [error.message for error in validator.iter_errors(registry)]
    structural: list[str] = []
    if not isinstance(registry.get("units"), list):
        structural.append("registry.units must be an array")
        return structural
    for index, unit in enumerate(registry["units"]):
        if not isinstance(unit, dict):
            structural.append(f"units[{index}] must be an object")
        elif not unit.get("id"):
            structural.append(f"units[{index}].id is required")
    return structural


def migrate_unit(unit: dict[str, Any]) -> dict[str, Any]:
    """Pure v1 -> v2 unit conversion (no side effects).

    - ``name`` becomes ``title``, ``file_pattern`` moves into ``scope.files``.
    - New fields default to empty/unknown values so a migrated unit is always
      valid under schema v2 without inventing facts (e.g. ``confidence: null``).
    - v1 units have no status; they are live knowledge, so they become
      ``active``.
    """
    migrated = copy.deepcopy(unit)
    migrated["title"] = unit.get("title") or unit.get("name") or unit.get("id", "")
    migrated["summary"] = unit.get("summary", "")
    migrated["rationale"] = unit.get("rationale", "")

    scope = unit.get("scope") if isinstance(unit.get("scope"), dict) else {}
    files = [item for item in (scope.get("files") or []) if item]
    legacy_pattern = unit.get("file_pattern")
    if legacy_pattern and legacy_pattern not in files:
        files.insert(0, legacy_pattern)
    migrated["scope"] = {
        "files": files,
        "symbols": [item for item in (scope.get("symbols") or []) if item],
    }

    migrated["evidence"] = unit.get("evidence", [])
    migrated["confidence"] = unit.get("confidence", None)
    migrated["owner"] = unit.get("owner", None)
    migrated["reviewer"] = unit.get("reviewer", None)
    migrated["status"] = unit.get("status") if unit.get("status") in UNIT_STATUSES else "active"
    migrated["risk_level"] = unit.get("risk_level", "LOW")
    migrated["knowledge_delta"] = unit.get("knowledge_delta", {"ops": []})
    migrated["related_docs"] = unit.get("related_docs", [])
    migrated["last_verified"] = unit.get("last_verified", "")
    migrated["code_hash"] = unit.get("code_hash", "")
    migrated["version"] = int(unit.get("version", 1))

    # v1-only keys are replaced by their v2 counterparts above.
    migrated.pop("file_pattern", None)
    migrated.pop("name", None)
    return migrated


def migrate_registry(registry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Migrate a registry document to v2. Returns (migrated, warnings).

    Idempotent: a document already at version >= 2 is returned unchanged.
    """
    if int(registry.get("version", 0)) >= 2:
        return copy.deepcopy(registry), []

    warnings: list[str] = []
    migrated = copy.deepcopy(registry)
    for index, unit in enumerate(migrated.get("units", [])):
        if not isinstance(unit, dict):
            warnings.append(f"units[{index}] is not an object; skipped.")
            continue
        migrated["units"][index] = migrate_unit(unit)
    migrated["version"] = 2
    return migrated, warnings
