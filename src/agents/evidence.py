from __future__ import annotations

"""Evidence agent: aggregate traceable evidence and owner suggestions.

Reads the discovery report and returns the **enriched candidate list** —
each candidate keeps its full draft fields and gains a recomputed
``confidence``, the aggregated ``evidence_ids``, and the owner suggestion
(``owner_inferred`` stays true until a human confirms). Knowledge must
consume this output, never the raw report, so enrichment is never bypassed.
"""

import json
from pathlib import Path
from typing import Any

from src.agents.base import Agent, register
from src.agents.schemas import CANDIDATE_SCHEMA, array_of, object_with
from src.evidence.confidence import compute_confidence


@register
class EvidenceAgent(Agent):
    name = "evidence"
    role = "Evidence-chain aggregation: commit history, confidence, owner inference"

    input_schema = object_with(
        properties={
            "discovery_report_path": {"type": "string", "minLength": 1},
            "settings": {"type": "object"},
        },
        required=["discovery_report_path"],
    )

    output_schema = object_with(
        properties={
            "candidates": array_of(CANDIDATE_SCHEMA),
        },
        required=["candidates"],
    )

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        report_path = Path(task["discovery_report_path"])
        with report_path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)

        enriched: list[dict[str, Any]] = []
        for candidate in report.get("candidates", []):
            evidence = candidate.get("evidence") or []
            enriched_candidate = dict(candidate)
            enriched_candidate["evidence_count"] = len(evidence)
            enriched_candidate["evidence_ids"] = [item.get("id", "") for item in evidence]
            enriched_candidate["confidence"] = compute_confidence(evidence, task.get("settings"))
            enriched_candidate["owner"] = candidate.get("owner")
            enriched_candidate["owner_inferred"] = bool(candidate.get("owner_inferred"))
            enriched.append(enriched_candidate)
        return {"candidates": enriched}
