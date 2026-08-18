from __future__ import annotations

"""Injection agent: pre-edit context selection for concrete files.

Wraps the existing injection pipeline (``src.inject.context``) and returns
the exact text an AI coding session would receive for each file — so a
pipeline run can prove what knowledge would have reached the developer.
Without a registry every file degrades to an unmatched preview instead of
failing.
"""

from pathlib import Path
from typing import Any

from src.agents.base import Agent, register
from src.agents.schemas import PREVIEW_SCHEMA, array_of, object_with
from src.inject.context import DEFAULT_MAX_TOKENS, build_context, format_context, knowledge_block


@register
class InjectionAgent(Agent):
    name = "injection"
    role = "Context selection: the minimal sufficient knowledge for concrete files"

    input_schema = object_with(
        properties={
            "repo": {"type": "string", "minLength": 1},
            "file_paths": array_of({"type": "string"}),
            "registry_path": {"type": "string"},
            "reports_path": {"type": "string"},
            "patches_path": {"type": "string"},
            "max_tokens": {"type": "integer", "minimum": 1},
        },
        required=["repo", "file_paths"],
    )

    output_schema = object_with(
        properties={"previews": array_of(PREVIEW_SCHEMA)},
        required=["previews"],
    )

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        repo_root = Path(task["repo"])
        max_tokens = int(task.get("max_tokens") or DEFAULT_MAX_TOKENS)

        # Registry resolution: explicit path -> the project's conventional
        # location -> unconfigured (every file reports unmatched, like the
        # inject CLI does for a project without knowledge).
        registry_path = task.get("registry_path")
        if not registry_path:
            conventional = repo_root / ".knowledge-ci" / "data" / "registry.json"
            registry_path = str(conventional) if conventional.is_file() else None
        if registry_path and not Path(registry_path).is_file():
            registry_path = None

        previews: list[dict[str, Any]] = []
        for file_path in task["file_paths"]:
            if registry_path is None:
                previews.append(
                    {
                        "file": file_path,
                        "matched": False,
                        "text": format_context(
                            {"matched": False, "file_path": file_path},
                            max_tokens=max_tokens,
                            include_feedback=False,
                        ),
                    }
                )
                continue
            context = build_context(
                file_path=file_path,
                project_root=repo_root,
                registry_path=registry_path,
                reports_path=task.get("reports_path"),
                patches_path=task.get("patches_path"),
            )
            if context.get("matched"):
                _, tokens = knowledge_block(context, max_tokens)
                preview = {
                    "file": file_path,
                    "matched": True,
                    "unit_id": context.get("unit_id", ""),
                    "text": format_context(context, max_tokens=max_tokens, include_feedback=False),
                    "estimated_tokens": tokens,
                }
            else:
                preview = {
                    "file": file_path,
                    "matched": False,
                    "text": format_context(context, max_tokens=max_tokens, include_feedback=False),
                }
            previews.append(preview)
        return {"previews": previews}
