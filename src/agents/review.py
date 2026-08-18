from __future__ import annotations

"""Review agent: human-review assistance for knowledge drafts.

For every draft it emits a diff-style summary and one recommendation:

- ``confirm``     — evidence >= 2 items, no conflicts, no inferred-only owner
- ``ask_owner``   — insufficient evidence or no owner at all
- ``human_review``— conflicts present or low confidence (verify manually)

Recommendations are advisory only; nothing is landed by this agent.
"""

from typing import Any

from src.agents.base import Agent, register
from src.evidence.confidence import is_sufficiently_evidenced

RECOMMENDATIONS = ("confirm", "ask_owner", "human_review")


@register
class ReviewAgent(Agent):
    name = "review"
    role = "Review assistance: diff summaries and recommendations for drafts"

    input_schema = {
        "type": "object",
        "required": ["drafts", "risks"],
        "properties": {
            "drafts": {"type": "array"},
            "risks": {"type": "array"},
        },
        "additionalProperties": True,
    }

    output_schema = {
        "type": "object",
        "required": ["reviews"],
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "recommendation", "summary"],
                    "properties": {
                        "id": {"type": "string"},
                        "recommendation": {"type": "string", "enum": list(RECOMMENDATIONS)},
                        "summary": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    }

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        drafts = task["drafts"]
        risks_by_id = {
            str(item.get("id", "")): item for item in task.get("risks", [])
        }

        reviews: list[dict[str, Any]] = []
        for draft in drafts:
            draft_id = str(draft.get("id", ""))
            risk = risks_by_id.get(draft_id, {})
            conflicts = list(risk.get("conflicts", []))
            warnings = list(risk.get("warnings", []))
            evidence = draft.get("evidence") or []
            confidence = draft.get("confidence")

            if conflicts:
                recommendation = "human_review"
                reason = "conflicts: " + "; ".join(conflicts)
            elif not is_sufficiently_evidenced(evidence) or not draft.get("owner"):
                recommendation = "ask_owner"
                reason = ("warnings: " + "; ".join(warnings)) if warnings else "insufficient evidence or missing owner"
            elif confidence is None or confidence < 0.5:
                recommendation = "human_review"
                reason = f"confidence too low ({confidence})"
            else:
                recommendation = "confirm"
                reason = f"evidence={len(evidence)}, confidence={confidence}, no conflicts"

            summary = (
                f"[{draft.get('title', '')}] "
                f"files={', '.join((draft.get('scope') or {}).get('files', []))}; "
                f"evidence={len(evidence)}; confidence={confidence}"
            )
            reviews.append(
                {
                    "id": draft_id,
                    "recommendation": recommendation,
                    "summary": summary,
                    "reason": reason,
                }
            )
        return {"reviews": reviews}
