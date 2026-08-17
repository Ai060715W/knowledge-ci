from __future__ import annotations

"""Confidence scoring for knowledge candidates.

The design document's example (``0.93`` for commit + incident + human answer)
is produced by the formula implemented here: a noisy-OR over distinct evidence
types, weighted by how authoritative each type is.

    confidence = 1 - Π (1 - weight[type])          over distinct evidence types

    weights: human_answer 0.9, incident 0.6, mr 0.5, issue 0.4, commit 0.3,
             code 0.2 (configurable via discovery.confidence_weights)

    Example: commit (0.3) + incident (0.6) + human_answer (0.9)
             = 1 - (0.7 · 0.4 · 0.1) = 0.972 → 0.97

No evidence yields ``None`` (unknown, not zero): the candidate is still a
``proposed`` draft until a human confirms it.
"""

from typing import Any

__all__ = [
    "DEFAULT_CONFIDENCE_WEIGHTS",
    "EVIDENCE_TYPES",
    "compute_confidence",
    "is_sufficiently_evidenced",
]

#: Evidence types accepted by the schema and recognized by the formula.
EVIDENCE_TYPES: tuple[str, ...] = (
    "code",
    "commit",
    "mr",
    "issue",
    "incident",
    "human_answer",
)

#: Default per-type weights: a human answer is the strongest signal, a bare
#: code location the weakest.
DEFAULT_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "code": 0.2,
    "commit": 0.3,
    "mr": 0.5,
    "issue": 0.4,
    "incident": 0.6,
    "human_answer": 0.9,
}

#: Minimum number of evidence items required before a candidate counts as
#: "sufficiently evidenced" (used to decide whether extra questions are asked).
MIN_EVIDENCE_ITEMS = 2


def _weights_from_settings(settings: dict[str, Any] | None) -> dict[str, float]:
    raw = (settings or {}).get("discovery", {}).get("confidence_weights") or {}
    weights = dict(DEFAULT_CONFIDENCE_WEIGHTS)
    for evidence_type in weights:
        if evidence_type in raw:
            try:
                value = float(raw[evidence_type])
            except (TypeError, ValueError):
                continue
            if 0.0 <= value <= 1.0:
                weights[evidence_type] = value
    return weights


def compute_confidence(
    evidence: list[dict[str, Any]] | None,
    settings: dict[str, Any] | None = None,
) -> float | None:
    """Compute confidence in [0, 1] from evidence items, or None when unknown.

    Unknown evidence types are ignored; duplicates count once per type.
    """
    items = evidence or []
    if not items:
        return None

    weights = _weights_from_settings(settings)
    remaining = 1.0
    for evidence_type in sorted({str(item.get("type", "")) for item in items if item.get("type")}):
        weight = weights.get(evidence_type)
        if weight is None:
            continue
        remaining *= 1.0 - weight

    confidence = 1.0 - remaining
    return round(min(1.0, max(0.0, confidence)), 2)


def is_sufficiently_evidenced(
    evidence: list[dict[str, Any]] | None,
    min_items: int = MIN_EVIDENCE_ITEMS,
) -> bool:
    """Whether a candidate has enough traceable evidence to skip extra questions."""
    return len(evidence or []) >= min_items
