from __future__ import annotations

"""Shared, strict JSON Schemas for the A2A agent contracts.

Every agent's input/output schemas are assembled from these fragments with
``additionalProperties: false``, so a message carrying undeclared fields or
malformed entries fails validation. This is the single source of truth for
the data shapes that flow between agents.
"""

from typing import Any

__all__ = [
    "CANDIDATE_SCHEMA",
    "EVIDENCE_ITEM_SCHEMA",
    "PREVIEW_SCHEMA",
    "QUESTION_SCHEMA",
    "REVIEW_ENTRY_SCHEMA",
    "RISK_ENTRY_SCHEMA",
    "SCOPE_SCHEMA",
    "TOP_MODULE_SCHEMA",
    "array_of",
    "object_with",
]

#: Risk levels used by the risk agent (registry enum compatible).
RISK_LEVELS = ["HIGH", "MEDIUM", "LOW"]

SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["files", "symbols"],
    "properties": {
        "files": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "symbols": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["code", "commit", "mr", "issue", "incident", "human_answer"],
        },
        "id": {"type": "string"},
        "short_id": {"type": "string"},
        "subject": {"type": "string"},
        "author": {"type": "string"},
        "role": {"type": "string"},
        "source": {"type": "string"},
        "note": {"type": "string"},
    },
    "additionalProperties": False,
}

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["zh", "en"],
    "properties": {
        "zh": {"type": "string"},
        "en": {"type": "string"},
    },
    "additionalProperties": False,
}

CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "title", "status"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "signal_kind": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
        "scope": SCOPE_SCHEMA,
        "evidence": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
        "evidence_count": {"type": "integer", "minimum": 0},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "owner": {"type": ["string", "null"]},
        "owner_inferred": {"type": "boolean"},
        "reviewer": {"type": ["string", "null"]},
        "status": {
            "type": "string",
            "enum": ["proposed", "under_review", "active", "outdated", "retired"],
        },
        "knowledge_delta": {
            "type": "object",
            "properties": {"ops": {"type": "array"}},
            "additionalProperties": False,
        },
        "related_docs": {"type": "array", "items": {"type": "string"}},
        "last_verified": {"type": ["string", "null"]},
        "code_hash": {"type": "string"},
        "version": {"type": "integer", "minimum": 1},
        "questions": {"type": "array", "items": QUESTION_SCHEMA},
    },
    "additionalProperties": False,
}

TOP_MODULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["module", "path", "score"],
    "properties": {
        "module": {"type": "string"},
        "path": {"type": "string"},
        "score": {"type": "number"},
    },
    "additionalProperties": False,
}

RISK_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "signal_risk", "review_risk", "conflicts", "warnings", "rationale"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "signal_risk": {"type": "string", "enum": RISK_LEVELS},
        "review_risk": {"type": "string", "enum": RISK_LEVELS},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

REVIEW_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "recommendation", "summary", "reason"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "recommendation": {"type": "string", "enum": ["confirm", "ask_owner", "human_review"]},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
        "questions": {"type": "array", "items": QUESTION_SCHEMA},
    },
    "additionalProperties": False,
}

PREVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["file", "matched", "text"],
    "properties": {
        "file": {"type": "string"},
        "matched": {"type": "boolean"},
        "unit_id": {"type": "string"},
        "text": {"type": "string"},
        "estimated_tokens": {"type": "integer"},
    },
    "additionalProperties": False,
}


def array_of(item_schema: dict[str, Any], min_items: int = 0) -> dict[str, Any]:
    """Build a strict array schema for ``item_schema``."""
    schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if min_items:
        schema["minItems"] = min_items
    return schema


def object_with(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build a strict object schema from properties + required list."""
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }
