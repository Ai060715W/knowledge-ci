from __future__ import annotations

"""Knowledge agent: turn enriched candidates into schema-v2 unit drafts.

Consumes the **evidence agent's enriched candidates** (never the raw
discovery report), so confidence/owner/evidence always come from the
enrichment stage. Each draft is a ``proposed`` unit with owner questions
attached; nothing is written to any registry here — landing is a human
action (``kc ask-owner``) or a later pipeline stage's PENDING patch.
"""

from typing import Any

from src.agents.base import Agent, register
from src.agents.schemas import CANDIDATE_SCHEMA, array_of, object_with
from src.evidence.questions import build_candidate_questions

#: Fields a v2 knowledge unit draft carries. ``signal_kind`` is schema-extra
#: metadata (allowed by the registry's additionalProperties) that the risk
#: agent uses for grading.
_DRAFT_FIELDS = (
    "id",
    "signal_kind",
    "title",
    "summary",
    "rationale",
    "scope",
    "evidence",
    "confidence",
    "owner",
    "owner_inferred",
    "reviewer",
    "status",
    "knowledge_delta",
    "related_docs",
    "last_verified",
    "code_hash",
    "version",
)


def _to_draft(candidate: dict[str, Any]) -> dict[str, Any]:
    draft = {field: candidate.get(field) for field in _DRAFT_FIELDS}
    draft["status"] = "proposed"
    draft["confidence"] = candidate.get("confidence")
    draft["reviewer"] = candidate.get("reviewer")
    # Normalize optional collections that upstream stages may omit, so the
    # strict output contract never sees None for an array field.
    draft["related_docs"] = list(candidate.get("related_docs") or [])
    scope = dict(candidate.get("scope") or {})
    scope.setdefault("files", [])
    scope.setdefault("symbols", [])
    draft["scope"] = scope
    draft["questions"] = build_candidate_questions(
        candidate.get("signal_kind", ""),
        candidate.get("evidence"),
    )
    return draft


@register
class KnowledgeAgent(Agent):
    name = "knowledge"
    role = "Knowledge draft generation from enriched candidates, plus owner questions"

    input_schema = object_with(
        properties={"candidates": array_of(CANDIDATE_SCHEMA)},
        required=["candidates"],
    )

    output_schema = object_with(
        properties={
            "drafts": array_of(CANDIDATE_SCHEMA),
            "question_count": {"type": "integer", "minimum": 0},
        },
        required=["drafts", "question_count"],
    )

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        drafts = [_to_draft(candidate) for candidate in task["candidates"]]
        question_count = sum(len(draft.get("questions", [])) for draft in drafts)
        return {"drafts": drafts, "question_count": question_count}
