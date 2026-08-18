from __future__ import annotations

"""Risk agent: risk grading, conflict and warning detection for drafts.

Rule-based and fully explainable:

- risk level from the draft's signal kind: bridge layers, dependency cycles,
  and revert history are HIGH; magic numbers and global instances are
  MEDIUM; structural-size signals are LOW.
- **conflicts** (force human review): evidence chains containing a revert
  commit, or drafts whose scope overlaps another draft.
- **warnings** (route to asking the owner): no traceable evidence, no owner,
  or an owner that is only inferred.
"""

from typing import Any

from src.agents.base import Agent, register

#: Signal kind -> suggested risk level (documented heuristic, v1).
_SIGNAL_RISK: dict[str, str] = {
    "magic_number": "MEDIUM",
    "global_instance": "MEDIUM",
    "bridge_compat": "HIGH",
    "long_function": "LOW",
    "long_class": "LOW",
    "dependency_cycle": "HIGH",
    "reverted_history": "HIGH",
}

DEFAULT_RISK = "MEDIUM"


@register
class RiskAgent(Agent):
    name = "risk"
    role = "Risk grading and evidence-conflict detection for knowledge drafts"

    input_schema = {
        "type": "object",
        "required": ["drafts"],
        "properties": {"drafts": {"type": "array"}},
        "additionalProperties": True,
    }

    output_schema = {
        "type": "object",
        "required": ["risks"],
        "properties": {
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "risk_level", "conflicts", "warnings"],
                    "properties": {
                        "id": {"type": "string"},
                        "risk_level": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                        "conflicts": {"type": "array", "items": {"type": "string"}},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    }

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
            risk_level = _SIGNAL_RISK.get(signal_kind, DEFAULT_RISK)

            rationale = (
                f"signal kind {signal_kind or 'unknown'} -> {risk_level}; "
                f"{len(evidence)} evidence item(s); {len(conflicts)} conflict(s), {len(warnings)} warning(s)"
            )
            risks.append(
                {
                    "id": str(draft.get("id", "")),
                    "risk_level": risk_level,
                    "conflicts": conflicts,
                    "warnings": warnings,
                    "rationale": rationale,
                }
            )
        return {"risks": risks}
