from __future__ import annotations

"""Review agent: human-review assistance for knowledge drafts.

For every draft it emits a diff-style summary and one **recommendation**:

- ``confirm``      — evidence >= 2 items, no conflicts, confirmed owner,
                     confidence >= 0.5 (0.5 = one mr or two commits of
                     evidence strength in the confidence formula)
- ``ask_owner``    — insufficient evidence or no owner; the draft's
                     question list is attached so the human loop can start
                     immediately (``kc ask-owner``)
- ``human_review`` — hard conflicts or low confidence (verify manually)

Recommendations are advisory only: a ``confirm`` is still just a
suggestion — the actual decision happens in the human pipeline
(``kc apply`` / ``kc ask-owner --confirm``), never in this agent.
"""

from typing import Any

from src.agents.base import Agent, register
from src.agents.schemas import CANDIDATE_SCHEMA, REVIEW_ENTRY_SCHEMA, RISK_ENTRY_SCHEMA, array_of, object_with
from src.evidence.confidence import is_sufficiently_evidenced

RECOMMENDATIONS = ("confirm", "ask_owner", "human_review")
CONFIRM_CONFIDENCE_THRESHOLD = 0.5


@register
class ReviewAgent(Agent):
    name = "review"
    role = "Review assistance: diff summaries and recommendations for drafts"

    input_schema = object_with(
        properties={
            "drafts": array_of(CANDIDATE_SCHEMA),
            "risks": array_of(RISK_ENTRY_SCHEMA),
        },
        required=["drafts", "risks"],
    )

    output_schema = object_with(
        properties={"reviews": array_of(REVIEW_ENTRY_SCHEMA)},
        required=["reviews"],
    )

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
            elif confidence is None or confidence < CONFIRM_CONFIDENCE_THRESHOLD:
                recommendation = "human_review"
                reason = f"confidence too low ({confidence})"
            else:
                recommendation = "confirm"
                reason = f"evidence={len(evidence)}, confidence={confidence}, no conflicts"

            entry: dict[str, Any] = {
                "id": draft_id,
                "recommendation": recommendation,
                "summary": (
                    f"[{draft.get('title', '')}] "
                    f"files={', '.join((draft.get('scope') or {}).get('files', []))}; "
                    f"evidence={len(evidence)}; confidence={confidence}"
                ),
                "reason": reason,
            }
            # Attach the draft's questions so the ask_owner loop can start
            # directly from the run report.
            if recommendation == "ask_owner":
                entry["questions"] = draft.get("questions", [])
            reviews.append(entry)
        return {"reviews": reviews}
