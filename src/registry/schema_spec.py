from __future__ import annotations

"""Pure JSON Schema documents for knowledge units and registries (schema v2).

Kept data-only and separate from logic so the contract is easy to review and
diff. Unknown unit fields are preserved (``additionalProperties: true``) to stay
forward-compatible, while known fields are type-checked strictly.
"""

#: JSON Schema for one knowledge unit (draft-07).
UNIT_V2_SCHEMA: dict = {
    "type": "object",
    "required": ["id", "title", "status", "version"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        # Legacy v1 display name; tolerated for unmigrated data.
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
        "scope": {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "symbols": {"type": "array", "items": {"type": "string", "minLength": 1}},
            },
            "additionalProperties": True,
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["code", "commit", "mr", "issue", "incident", "human_answer"],
                    },
                    "id": {"type": "string"},
                    "source": {"type": "string"},
                    "note": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "confidence": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
        },
        "owner": {"type": ["string", "null"]},
        "reviewer": {"type": ["string", "null"]},
        # Owner values derived by tooling (git blame/CODEOWNERS) are suggestions.
        "owner_inferred": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": ["proposed", "under_review", "active", "outdated", "retired"],
        },
        # Legacy v1 file glob; tolerated for unmigrated data.
        "file_pattern": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "knowledge_delta": {
            "type": "object",
            "properties": {"ops": {"type": "array"}},
            "additionalProperties": True,
        },
        "related_docs": {"type": "array", "items": {"type": "string"}},
        "last_verified": {"type": ["string", "null"]},
        "code_hash": {"type": ["string", "null"]},
        "version": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": True,
}

#: JSON Schema for a whole registry document (schema v2).
REGISTRY_V2_SCHEMA: dict = {
    "type": "object",
    "required": ["version", "units"],
    "properties": {
        "version": {"type": "integer", "const": 2},
        "last_updated": {"type": "string"},
        "units": {"type": "array", "items": UNIT_V2_SCHEMA},
    },
    "additionalProperties": True,
}
