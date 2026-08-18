from __future__ import annotations

"""Risk agent: signal risk, review risk, conflicts and warnings for drafts.

Two distinct risk concepts, both rule-based and fully explainable:

- **signal_risk** — how dangerous the detected code structure itself is
  (bridge layers, dependency cycles, and revert history are HIGH; magic
  numbers and global instances are MEDIUM; structural-size signals are LOW).
- **review_risk** — the overall risk of trusting this draft right now,
  derived from the evidence quality on top of the signal:

      any hard conflict                        -> HIGH
      >= 2 warnings (e.g. no evidence, no owner) -> HIGH
      confidence is not None and confidence < 0.3 -> HIGH
      otherwise                                -> signal_risk

Hard conflicts (revert commits, scope overlaps) force human review; soft
warnings (no evidence, missing/inferred owner) route to asking the owner.
"""

from typing import Any

from src.agents.base import Agent, register
from src.agents.schemas import CANDIDATE_SCHEMA, RISK_ENTRY_SCHEMA, array_of, object_with

#: Signal kind -> suggested signal risk (documented heuristic, v1).
_SIGNAL_RISK: dict[str, str] = {
    "magic_number": "MEDIUM",
    "global_instance": "MEDIUM",
    "bridge_compat": "HIGH",
    "long_function": "LOW",
    "long_class": "LOW",
    "dependency_cycle": "HIGH",
    "reverted_history": "HIGH",
}

DEFAULT_SIGNAL_RISK = "MEDIUM"
#: Evidence-quality threshold below which review risk jumps to HIGH.
#: 0.5 (the review agent's confirm threshold) equals one mr item (0.5) or
#: two commit items (1 - 0.7*0.7 = 0.51) in the confidence formula.
LOW_CONFIDENCE_THRESHOLD = 0.3
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _review_risk(signal_risk: str, conflicts: list[str], warnings: list[str], confidence: Any) -> str:
    if conflicts:
        return "HIGH"
    if len(warnings) >= 2:
        return "HIGH"
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        return "HIGH"
    return signal_risk


@register
class RiskAgent(Agent):
    name = "risk"
    role = "Signal/review risk grading plus conflict and warning detection for drafts"

    input_schema = object_with(
        properties={"drafts": array_of(CANDIDATE_SCHEMA)},
        required=["drafts"],
    )

    output_schema = object_with(
        properties={"risks": array_of(RISK_ENTRY_SCHEMA)},
        required=["risks"],
    )

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        drafts = task["drafts"]
        file_owners: dict[str, str] = {}
        risks: list[dict[str, Any]] = []

        for draft in drafts:
            # Hard conflicts force human review; warnings route to ask_owner.
            conflicts: list[str] = []
            warnings: list[str] = []
            evidence = draft.get("evidence") or []
            evidence_roles = {str(item.get("role", "")) for item in evidence}

            if "reverted" in evidence_roles:
                conflicts.append("evidence chain contains a revert commit")
            if not evidence:
                warnings.append("no traceable evidence")
            owner = draft.get("owner")
            if not owner:
                warnings.append("no owner (inferred or confirmed)")
            elif draft.get("owner_inferred"):
                warnings.append("owner is only inferred (unconfirmed)")

            files = (draft.get("scope") or {}).get("files") or []
            for path in files:
                previous = file_owners.get(path)
                if previous is not None and previous != draft.get("id"):
                    conflicts.append(f"scope overlaps draft {previous}")
                else:
                    file_owners[path] = str(draft.get("id", ""))

            signal_kind = draft.get("signal_kind", "")
            signal_risk = _SIGNAL_RISK.get(signal_kind, DEFAULT_SIGNAL_RISK)
            review_risk = _review_risk(signal_risk, conflicts, warnings, draft.get("confidence"))

            rationale = (
                f"signal kind {signal_kind or 'unknown'} -> signal_risk {signal_risk}; "
                f"review_risk {review_risk} ({len(conflicts)} conflict(s), "
                f"{len(warnings)} warning(s), confidence {draft.get('confidence')})"
            )
            risks.append(
                {
                    "id": str(draft.get("id", "")),
                    "signal_risk": signal_risk,
                    "review_risk": review_risk,
                    "conflicts": conflicts,
                    "warnings": warnings,
                    "rationale": rationale,
                }
            )
        return {"risks": risks}
