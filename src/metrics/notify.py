from __future__ import annotations

"""Knowledge-change notifications grouped by owner (Phase 2, Plan C).

Scans the patch directory (PENDING = awaiting review, APPLIED = landed
changes) together with the registry's current unit states, then produces a
per-owner summary: who has knowledge waiting for review and whose knowledge
changed. v1 only writes a local JSON report; the pure-data
``build_notification_report`` is deliberately separated from the file IO so a
future webhook/channel delivery can reuse the same report document.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "UNASSIGNED_OWNER",
    "build_notification_report",
    "write_notification_report",
]

#: Bucket for patches whose unit cannot be resolved to an owner (unit missing
#: from the registry or ``owner: null``).
UNASSIGNED_OWNER = "(unassigned)"

#: Patch statuses mapped onto notification buckets. REJECTED patches are
#: counted separately (never mixed into "changed") so future metrics can reuse
#: the same scan; any other/unknown status is ignored with a warning.
_PENDING = "PENDING"
_APPLIED = "APPLIED"
_REJECTED = "REJECTED"

#: Registry unit states that count as owner to-dos beyond pending patches.
_UNDER_REVIEW = "under_review"
_OUTDATED = "outdated"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _scan_patches(patches_path: str | Path | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Read every ``patch_*.json``. Corrupt files and patches without a
    ``unit_id`` are skipped and reported as warnings instead of crashing."""
    patches: list[dict[str, Any]] = []
    warnings: list[str] = []
    if patches_path is None:
        return patches, warnings
    directory = Path(patches_path)
    if not directory.is_dir():
        return patches, warnings
    for patch_file in sorted(directory.glob("patch_*.json")):
        try:
            patch = _load_json(patch_file)
        except (OSError, json.JSONDecodeError) as error:
            warnings.append(f"skipped unreadable patch file {patch_file.name}: {error}")
            continue
        if not isinstance(patch, dict) or not patch.get("unit_id"):
            warnings.append(f"skipped patch file {patch_file.name}: missing unit_id")
            continue
        status = str(patch.get("status", "")).upper()
        if status not in {_PENDING, _APPLIED, _REJECTED}:
            warnings.append(f"skipped patch file {patch_file.name}: unknown status {status!r}")
            continue
        patch["_source_file"] = patch_file.name
        patches.append(patch)
    return patches, warnings


def _patch_summary(patch: dict[str, Any]) -> dict[str, Any]:
    """Compact per-patch entry; timestamps stay on each item so consumers may
    apply their own time-window filtering (v1 reports the full history)."""
    return {
        "patch_id": patch.get("patch_id") or patch.get("_source_file", ""),
        "unit_id": patch.get("unit_id"),
        "risk_level": patch.get("risk_level"),
        "commit": str(patch.get("commit", ""))[:8] or None,
        "new_version": patch.get("new_version"),
        "generated_at": patch.get("generated_at"),
        "status_reason": patch.get("status_reason"),
    }


def build_notification_report(
    registry_path: str | Path,
    patches_path: str | Path | None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate pending/changed patches and unit states per owner.

    Raises ``FileNotFoundError`` when the registry does not exist — owners can
    only be resolved from the registry, so a missing one is a configuration
    error rather than empty data.
    """
    registry_file = Path(registry_path)
    if not registry_file.is_file():
        raise FileNotFoundError(f"Registry not found: {registry_file}")
    registry = _load_json(registry_file)

    units: dict[str, dict[str, Any]] = {
        str(unit.get("id")): unit for unit in registry.get("units", []) if unit.get("id")
    }
    patches, warnings = _scan_patches(patches_path)

    now = (generated_at or datetime.now(timezone.utc)).replace(microsecond=0)
    owners: dict[str, dict[str, Any]] = {}

    def bucket(owner: str) -> dict[str, Any]:
        return owners.setdefault(
            owner,
            {
                "pending_review": [],
                "changed": [],
                "rejected": [],
                "units": {},
                "units_under_review": [],
                "units_outdated": [],
            },
        )

    # Unit states: every unit lands on its owner's board; under_review and
    # outdated units are explicit to-dos beyond the patch buckets.
    for unit_id, unit in sorted(units.items()):
        owner = str(unit.get("owner") or UNASSIGNED_OWNER)
        entry = bucket(owner)
        status = str(unit.get("status", ""))
        entry["units"][unit_id] = status
        if status == _UNDER_REVIEW:
            entry["units_under_review"].append(unit_id)
        elif status == _OUTDATED:
            entry["units_outdated"].append(unit_id)

    # Patches: resolved through the registry to find the owning person/team.
    for patch in patches:
        unit = units.get(str(patch.get("unit_id")))
        owner = str((unit or {}).get("owner") or UNASSIGNED_OWNER)
        entry = bucket(owner)
        summary = _patch_summary(patch)
        status = str(patch.get("status", "")).upper()
        if status == _PENDING:
            entry["pending_review"].append(summary)
        elif status == _APPLIED:
            entry["changed"].append(summary)
        else:  # REJECTED — counted, never mixed into "changed"
            entry["rejected"].append(summary)

    # Newest first within each bucket so the report reads like an inbox.
    for entry in owners.values():
        for key in ("pending_review", "changed", "rejected"):
            entry[key].sort(key=lambda item: str(item.get("generated_at") or ""), reverse=True)

    status_distribution: dict[str, int] = {}
    for unit in units.values():
        status = str(unit.get("status", "unknown"))
        status_distribution[status] = status_distribution.get(status, 0) + 1

    summary = {
        "owners": len(owners),
        "pending_review": sum(len(entry["pending_review"]) for entry in owners.values()),
        "changed": sum(len(entry["changed"]) for entry in owners.values()),
        "rejected": sum(len(entry["rejected"]) for entry in owners.values()),
        "units_under_review": sum(len(entry["units_under_review"]) for entry in owners.values()),
        "units_outdated": sum(len(entry["units_outdated"]) for entry in owners.values()),
        "status_distribution": status_distribution,
        "warnings": len(warnings),
    }

    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "summary": summary,
        "owners": owners,
        "warnings": warnings,
        "delivery": {
            "channels": ["local_file"],
            "note": (
                "v1 writes this report to reports/notify_<ts>.json only. Channel delivery "
                "(webhook/IM) is a planned extension point: reuse build_notification_report() "
                "and post the document from the caller."
            ),
        },
    }


def write_notification_report(
    report: dict[str, Any],
    reports_path: str | Path,
    timestamp: str | None = None,
) -> Path:
    """Persist the report as ``reports/notify_<ts>.json`` and return the path."""
    output_dir = Path(reports_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"notify_{stamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path
