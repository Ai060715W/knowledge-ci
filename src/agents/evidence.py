from __future__ import annotations

"""Evidence agent: aggregate traceable evidence and owner suggestions.

Reads a discovery report (plan 2 output) and produces one evidence summary
per candidate: item count, confidence, evidence ids, and the inferred owner
(always a suggestion — ``owner_inferred`` stays true until a human confirms).
"""

import json
from pathlib import Path
from typing import Any

from src.agents.base import Agent, register
from src.evidence.confidence import compute_confidence


@register
class EvidenceAgent(Agent):
    name = "evidence"
    role = "Evidence-chain aggregation: commit history, confidence, owner inference"

    input_schema = {
        "type": "object",
        "required": ["discovery_report_path"],
        "properties": {
            "discovery_report_path": {"type": "string", "minLength": 1},
            "settings": {"type": "object"},
        },
        "additionalProperties": True,
    }

    output_schema = {
        "type": "object",
        "required": ["enriched"],
        "properties": {
            "enriched": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "evidence_count", "evidence_ids"],
                    "properties": {
                        "id": {"type": "string"},
                        "module": {"type": "string"},
                        "evidence_count": {"type": "integer"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": ["number", "null"]},
                        "owner": {"type": ["string", "null"]},
                        "owner_inferred": {"type": "boolean"},
                    },
                },
            }
        },
    }

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        report_path = Path(task["discovery_report_path"])
        with report_path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)

        enriched: list[dict[str, Any]] = []
        for candidate in report.get("candidates", []):
            evidence = candidate.get("evidence") or []
            enriched.append(
                {
                    "id": candidate.get("id", ""),
                    "module": (candidate.get("scope") or {}).get("files", [""])[0]
                    if isinstance(candidate.get("scope"), dict)
                    else "",
                    "evidence_count": len(evidence),
                    "evidence_ids": [item.get("id", "") for item in evidence],
                    "confidence": compute_confidence(evidence, task.get("settings")),
                    "owner": candidate.get("owner"),
                    "owner_inferred": bool(candidate.get("owner_inferred")),
                }
            )
        return {"enriched": enriched}
