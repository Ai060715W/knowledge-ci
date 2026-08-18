from __future__ import annotations

"""Observability: the design document's four KPIs with explainable formulas.

Every metric entry carries its formula, numerator/denominator, and the exact
calculation notes, so a ``metrics.json`` can be read and challenged without
opening the source. Missing inputs degrade to ``value: null`` with a note —
never a crash and never an invented number.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import CONFIG_DEFAULTS
from src.freshness.layers import layer_time
from src.registry.matcher import match_unit_record
from src.registry.schema import is_injectable
from src.registry.store import RegistryStore

__all__ = [
    "compute_confirmation_rate",
    "compute_coverage",
    "compute_freshness_rate",
    "compute_hit_rate",
    "compute_metrics",
    "status_distribution",
]


def _metric(
    key: str,
    formula: str,
    note: str,
    value: float | None,
    numerator: float | int,
    denominator: float | int,
) -> dict[str, Any]:
    return {
        "key": key,
        "formula": formula,
        "note": note,
        "value": round(value, 4) if value is not None else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _load_registry(registry_path: str | Path) -> dict[str, Any] | None:
    try:
        return RegistryStore(registry_path=registry_path).load_registry()
    except (OSError, json.JSONDecodeError):
        return None


def _latest_discovery_report(reports_path: str | Path | None) -> dict[str, Any] | None:
    if not reports_path:
        return None
    reports_dir = Path(reports_path)
    if not reports_dir.is_dir():
        return None
    candidates = sorted(reports_dir.glob("discovery_*.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def compute_coverage(
    registry: dict[str, Any] | None,
    discovery_report: dict[str, Any] | None,
    registry_path: str | Path | None,
) -> dict[str, Any]:
    """Coverage: fraction of Top-K high-value modules with active knowledge.

    Formula: |top modules matched to an active unit| / |top modules|.
    """
    if registry is None:
        return _metric("coverage", "covered_top_k / top_k", "registry unavailable", None, 0, 0)
    if discovery_report is None or not discovery_report.get("top_modules"):
        return _metric(
            "coverage",
            "covered_top_k / top_k",
            "no discovery report (run kc discover first)",
            None,
            0,
            0,
        )
    top_modules = discovery_report.get("top_modules", [])
    covered = 0
    for entry in top_modules:
        if not registry_path:
            continue
        unit = match_unit_record(entry.get("path", ""), registry_path)
        if unit is not None and is_injectable(unit):
            covered += 1
    return _metric(
        "coverage",
        "covered_top_k / top_k",
        "covered = top module paths matched to an active knowledge unit",
        covered / len(top_modules) if top_modules else None,
        covered,
        len(top_modules),
    )


def compute_freshness_rate(
    registry: dict[str, Any] | None,
    repo_root: str | Path,
    time_filter_days: float = 30.0,
) -> dict[str, Any]:
    """Freshness: fraction of active units whose anchor is newer than their files.

    Formula: fresh_active / active. Freshness reuses the layer-1 time check
    (code_hash -> last_verified -> fallback window) so the KPI and the
    pipeline share one definition.
    """
    if registry is None:
        return _metric("freshness_rate", "fresh_active / active", "registry unavailable", None, 0, 0)
    active_units = [unit for unit in registry.get("units", []) if is_injectable(unit)]
    if not active_units:
        return _metric("freshness_rate", "fresh_active / active", "no active units", None, 0, 0)
    fresh = 0
    for unit in active_units:
        verdict = layer_time(repo_root, unit, fallback_days=time_filter_days)
        if verdict.fresh:
            fresh += 1
    return _metric(
        "freshness_rate",
        "fresh_active / active",
        "fresh = active unit with no commits touching its scope files after its anchor",
        fresh / len(active_units),
        fresh,
        len(active_units),
    )


def compute_hit_rate(feedback_path: str | Path | None) -> dict[str, Any]:
    """Hit rate: fraction of injected contexts marked adopted by users.

    Formula: adopted_records / feedback_records. Adoption is recorded via the
    feedback endpoint's ``adopted=true`` parameter (self-reported).
    """
    if not feedback_path:
        return _metric("hit_rate", "adopted / total", "feedback_path not configured", None, 0, 0)
    path = Path(feedback_path)
    if not path.is_file():
        return _metric("hit_rate", "adopted / total", "no feedback records yet", None, 0, 0)
    total = 0
    adopted = 0
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            record = json.loads(raw_line)
            total += 1
            if record.get("adopted") is True:
                adopted += 1
    except (OSError, json.JSONDecodeError):
        return _metric("hit_rate", "adopted / total", "feedback log unreadable", None, 0, 0)
    if total == 0:
        return _metric("hit_rate", "adopted / total", "no feedback records yet", None, 0, 0)
    return _metric(
        "hit_rate",
        "adopted / total",
        "adopted = feedback records with adopted=true (self-reported via the feedback endpoint)",
        adopted / total,
        adopted,
        total,
    )


def compute_confirmation_rate(patches_path: str | Path | None) -> dict[str, Any]:
    """Confirmation rate: fraction of reviewed patches that humans accepted.

    Formula: APPLIED / (APPLIED + REJECTED). PENDING patches are still in
    review and excluded from both sides. v1 uses patch decisions as the
    observable proxy for knowledge confirmation; the registry status
    distribution is reported separately for context.
    """
    if not patches_path:
        return _metric("confirmation_rate", "applied / (applied + rejected)", "patches_path not configured", None, 0, 0)
    patches_dir = Path(patches_path)
    if not patches_dir.is_dir():
        return _metric("confirmation_rate", "applied / (applied + rejected)", "no patches directory", None, 0, 0)
    applied = 0
    rejected = 0
    pending = 0
    for patch_file in patches_dir.glob("patch_*.json"):
        try:
            status = json.loads(patch_file.read_text(encoding="utf-8")).get("status")
        except (OSError, json.JSONDecodeError):
            continue
        if status == "APPLIED":
            applied += 1
        elif status == "REJECTED":
            rejected += 1
        elif status == "PENDING":
            pending += 1
    decisions = applied + rejected
    if decisions == 0:
        return _metric(
            "confirmation_rate",
            "applied / (applied + rejected)",
            f"no reviewed patches yet ({pending} pending)",
            None,
            0,
            0,
        )
    return _metric(
        "confirmation_rate",
        "applied / (applied + rejected)",
        f"decisions over patch files; {pending} PENDING patches excluded from both sides",
        applied / decisions,
        applied,
        decisions,
    )


def status_distribution(registry: dict[str, Any] | None) -> dict[str, Any]:
    """Registry unit counts per status (context for the confirmation rate)."""
    if registry is None:
        return {"note": "registry unavailable", "counts": {}}
    counts = Counter(
        unit.get("status") or "active" for unit in registry.get("units", [])
    )
    return {"note": "unit counts per status in registry.json", "counts": dict(sorted(counts.items()))}


def compute_metrics(
    repo_root: str | Path,
    registry_path: str | Path,
    reports_path: str | Path | None = None,
    patches_path: str | Path | None = None,
    feedback_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path]:
    """Compute the four KPIs and write ``metrics.json``. Returns (report, path)."""
    root = Path(repo_root).resolve()
    output_dir = (Path(out_dir) if out_dir else root / ".knowledge-ci" / "data" / "metrics").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sections = settings or CONFIG_DEFAULTS
    time_filter_days = float((sections.get("freshness") or {}).get("time_filter_days", 30))

    registry = _load_registry(registry_path)
    discovery_report = _latest_discovery_report(reports_path)

    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repo": str(root),
        "registry_path": str(Path(registry_path).resolve()),
        "metrics": [
            compute_coverage(registry, discovery_report, registry_path),
            compute_freshness_rate(registry, root, time_filter_days=time_filter_days),
            compute_hit_rate(feedback_path),
            compute_confirmation_rate(patches_path),
        ],
        "status_distribution": status_distribution(registry),
    }
    output_path = output_dir / "metrics.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, output_path
