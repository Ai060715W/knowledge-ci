from __future__ import annotations

"""Knowledge agent: turn candidates into schema-v2 knowledge unit drafts.

Each draft is a ``proposed`` unit with questions attached; nothing is written
to any registry here — landing is a human action (``kc ask-owner``) or a
later pipeline stage's PENDING patch proposal.
"""

import json
from pathlib import Path
from typing import Any

from src.agents.base import Agent, register
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
    questions = build_candidate_questions(
        candidate.get("signal_kind", ""),
        candidate.get("evidence"),
    )
    draft["questions"] = questions
    return draft


@register
class KnowledgeAgent(Agent):
    name = "knowledge"
    role = "Knowledge draft generation from candidates, plus owner questions"

    input_schema = {
        "type": "object",
        "required": ["discovery_report_path"],
        "properties": {
            "discovery_report_path": {"type": "string", "minLength": 1},
        },
        "additionalProperties": True,
    }

    output_schema = {
        "type": "object",
        "required": ["drafts", "question_count"],
        "properties": {
            "drafts": {"type": "array"},
            "question_count": {"type": "integer"},
        },
    }

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        report_path = Path(task["discovery_report_path"])
        with report_path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)

        drafts = [_to_draft(candidate) for candidate in report.get("candidates", [])]
        question_count = sum(len(draft.get("questions", [])) for draft in drafts)
        return {"drafts": drafts, "question_count": question_count}
