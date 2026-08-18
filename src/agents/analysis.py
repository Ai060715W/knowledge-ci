from __future__ import annotations

"""Analysis agent: repository structure, hotspots, and anomaly signals.

Wraps plan 1 (``src.discovery``): builds the dependency graph, scores
modules, detects structural signals, and writes the discovery report.
"""

from pathlib import Path
from typing import Any

from src.agents.base import Agent, register
from src.discovery.discover import run_discovery


@register
class AnalysisAgent(Agent):
    name = "analysis"
    role = "AST/dependency-graph analysis, Top-K module scoring, structural anomaly signals"

    input_schema = {
        "type": "object",
        "required": ["repo"],
        "properties": {
            "repo": {"type": "string", "minLength": 1},
            "settings": {"type": "object"},
            "top_k": {"type": "integer", "minimum": 1},
            "out_dir": {"type": "string"},
            "registry_path": {"type": "string"},
        },
        "additionalProperties": True,
    }

    output_schema = {
        "type": "object",
        "required": ["report_path", "modules_scanned", "candidates", "top_modules"],
        "properties": {
            "report_path": {"type": "string"},
            "modules_scanned": {"type": "integer"},
            "candidates": {"type": "integer"},
            "top_modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["module", "path", "score"],
                    "properties": {
                        "module": {"type": "string"},
                        "path": {"type": "string"},
                        "score": {"type": "number"},
                    },
                },
            },
        },
    }

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        report, output_path = run_discovery(
            repo_root=task["repo"],
            settings=task.get("settings"),
            top_k=task.get("top_k"),
            out_dir=task.get("out_dir", "."),
            registry_path=task.get("registry_path"),
            use_cache=True,
        )
        return {
            "report_path": str(output_path),
            "modules_scanned": int(report.get("modules_scanned", 0)),
            "candidates": int(report.get("candidate_count", 0)),
            "top_modules": [
                {
                    "module": entry["module"],
                    "path": entry["path"],
                    "score": entry["score"],
                }
                for entry in report.get("top_modules", [])
            ],
        }
