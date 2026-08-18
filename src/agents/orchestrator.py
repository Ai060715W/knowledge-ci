from __future__ import annotations

"""A2A pipeline orchestrator: sequential, schema-validated, graceful.

Runs the registered agents in a fixed order (analysis → evidence →
knowledge → risk → patch proposals → review → injection) inside one process.
Every agent message is validated against its declared JSON schemas; a
failing stage is recorded and the pipeline degrades instead of crashing, so
one broken step never loses the rest of the run. A future distributed A2A
runtime would replace this in-process loop while keeping the agent
contracts (``AGENTS`` + schemas) unchanged.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git import Repo

from src.agents.base import AGENTS, describe_agents
from src.patch.delta import delta_to_text
from src.patch.generator import next_patch_id
from src.registry.matcher import match_unit_record

__all__ = ["PIPELINE_ORDER", "run_pipeline"]

#: Fixed sequential order. ``patch`` is an internal materialization stage
#: (PENDING proposals), not a registered agent.
PIPELINE_ORDER: tuple[str, ...] = (
    "analysis",
    "evidence",
    "knowledge",
    "risk",
    "patch",
    "review",
    "injection",
)


def _head_commit(repo_root: Path) -> str | None:
    repo = None
    try:
        repo = Repo(repo_root)
        return repo.head.commit.hexsha
    except Exception:
        return None
    finally:
        if repo is not None:
            try:
                repo.close()
            except Exception:
                pass


def _run_agent_stage(name: str, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Execute one registered agent with input/output schema validation."""
    agent_class = AGENTS[name]
    input_errors = agent_class.validate_input(task)
    if input_errors:
        return {
            "name": name,
            "status": "failed",
            "error": "input schema: " + "; ".join(input_errors),
        }
    output = agent_class().run(task)
    output_errors = agent_class.validate_output(output)
    if output_errors:
        return {
            "name": name,
            "status": "failed",
            "error": "output schema: " + "; ".join(output_errors),
        }
    state[name] = output
    return {"name": name, "status": "ok", "summary": _stage_summary(name, output)}


def _stage_summary(name: str, output: dict[str, Any]) -> str:
    if name == "analysis":
        return f"modules={output.get('modules_scanned', 0)}, candidates={output.get('candidates', 0)}"
    if name == "evidence":
        return f"enriched={len(output.get('enriched', []))} candidates"
    if name == "knowledge":
        return f"drafts={len(output.get('drafts', []))}, questions={output.get('question_count', 0)}"
    if name == "risk":
        return f"risks={len(output.get('risks', []))} graded"
    if name == "review":
        return f"reviews={len(output.get('reviews', []))} recommendations"
    if name == "injection":
        return f"previews={len(output.get('previews', []))} files"
    return "ok"


def _patch_stage(state: dict[str, Any], registry_path: str | Path | None, patches_path: str | Path | None) -> dict[str, Any]:
    """Materialize PENDING patch proposals for drafts matching existing units."""
    drafts = (state.get("knowledge") or {}).get("drafts", [])
    risks_by_id = {str(item.get("id", "")): item for item in (state.get("risk") or {}).get("risks", [])}
    if registry_path is None or patches_path is None:
        return {
            "name": "patch",
            "status": "ok",
            "summary": "0 proposals (registry or patches path not configured)",
            "proposals": [],
            "skipped": len(drafts),
        }

    registry_file = Path(registry_path)
    try:
        registry = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"name": "patch", "status": "failed", "error": f"registry unreadable: {registry_file}"}

    patches_dir = Path(patches_path)
    patches_dir.mkdir(parents=True, exist_ok=True)

    proposals: list[dict[str, Any]] = []
    skipped = 0
    for draft in drafts:
        files = (draft.get("scope") or {}).get("files") or []
        matched_unit = None
        for file_path in files:
            matched_unit = match_unit_record(file_path, registry_file)
            if matched_unit is not None:
                break
        if matched_unit is None:
            skipped += 1
            continue

        unit_id = matched_unit.get("id", "")
        old_text = delta_to_text(matched_unit.get("knowledge_delta"))
        new_text = str(draft.get("summary") or "")
        ops = [{"insert": new_text}] if not old_text else [{"delete": len(old_text)}, {"insert": new_text}]
        patch_id = next_patch_id(patches_dir)
        risk = risks_by_id.get(str(draft.get("id", "")), {})
        patch = {
            "patch_id": patch_id,
            "status": "PENDING",
            "unit_id": unit_id,
            "old_version": int(matched_unit.get("version", 0)),
            "new_version": int(matched_unit.get("version", 0)) + 1,
            "risk_level": risk.get("risk_level", matched_unit.get("risk_level", "LOW")),
            "delta_ops": ops,
            "reasoning": f"[agent pipeline] draft {draft.get('id', '')}: {draft.get('summary', '')[:120]}",
            "affected_files": files,
            "related_docs": draft.get("related_docs", []),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "model": "agent-pipeline",
            "source": "agents",
        }
        output_path = patches_dir / f"patch_{patch_id}.json"
        output_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        proposals.append({"patch_id": patch_id, "unit_id": unit_id, "path": str(output_path)})

    return {
        "name": "patch",
        "status": "ok",
        "summary": f"{len(proposals)} proposal(s), {skipped} draft(s) without matching unit",
        "proposals": proposals,
        "skipped": skipped,
    }


def _injection_file_paths(state: dict[str, Any], limit: int = 5) -> list[str]:
    top_modules = (state.get("analysis") or {}).get("top_modules", [])
    return [entry.get("path", "") for entry in top_modules[:limit] if entry.get("path")]


def run_pipeline(
    repo_root: str | Path,
    settings: dict[str, Any] | None = None,
    top_k: int | None = None,
    out_dir: str | Path = ".",
    registry_path: str | Path | None = None,
    reports_path: str | Path | None = None,
    patches_path: str | Path | None = None,
    stop_after: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run the full agent pipeline. Returns (report, path).

    ``stop_after`` names the last stage to execute (e.g. ``knowledge``);
    later stages are recorded as skipped. Failures degrade gracefully.
    """
    root = Path(repo_root).resolve()
    output_dir = Path(out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    state: dict[str, Any] = {}
    pipeline: list[dict[str, Any]] = []
    stopped = False

    for name in PIPELINE_ORDER:
        if stopped:
            pipeline.append({"name": name, "status": "skipped", "reason": f"stop_after={stop_after}"})
            continue

        if name == "patch":
            pipeline.append(_patch_stage(state, registry_path, patches_path))
        elif name == "evidence" and "analysis" not in state:
            pipeline.append({"name": name, "status": "skipped", "reason": "analysis failed"})
        elif name == "knowledge" and "analysis" not in state:
            pipeline.append({"name": name, "status": "skipped", "reason": "analysis failed"})
        elif name == "risk" and "knowledge" not in state:
            pipeline.append({"name": name, "status": "skipped", "reason": "knowledge unavailable"})
        elif name == "review" and ("knowledge" not in state or "risk" not in state):
            pipeline.append({"name": name, "status": "skipped", "reason": "knowledge/risk unavailable"})
        else:
            try:
                if name == "analysis":
                    task = {
                        "repo": str(root),
                        "settings": settings,
                        "top_k": top_k,
                        "out_dir": str(output_dir),
                        "registry_path": str(registry_path) if registry_path else None,
                    }
                elif name == "evidence":
                    task = {
                        "discovery_report_path": state["analysis"]["report_path"],
                        "settings": settings,
                    }
                elif name == "knowledge":
                    task = {"discovery_report_path": state["analysis"]["report_path"]}
                elif name == "risk":
                    task = {"drafts": state["knowledge"]["drafts"]}
                elif name == "review":
                    task = {"drafts": state["knowledge"]["drafts"], "risks": state["risk"]["risks"]}
                elif name == "injection":
                    task = {
                        "repo": str(root),
                        "file_paths": _injection_file_paths(state),
                        "registry_path": str(registry_path) if registry_path else None,
                        "reports_path": str(reports_path) if reports_path else None,
                        "patches_path": str(patches_path) if patches_path else None,
                    }
                else:  # pragma: no cover - pipeline order is fixed
                    raise KeyError(f"unknown pipeline stage: {name}")
                # None values violate the schemas' type constraints; optional
                # fields are simply omitted from the task.
                task = {key: value for key, value in task.items() if value is not None}
                pipeline.append(_run_agent_stage(name, task, state))
            except Exception as error:  # noqa: BLE001 - degrade gracefully per stage
                pipeline.append({"name": name, "status": "failed", "error": f"{type(error).__name__}: {error}"})

        if stop_after and name == stop_after:
            stopped = True

    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repo": str(root),
        "head_commit": _head_commit(root),
        "agents": describe_agents(),
        "pipeline": pipeline,
        "drafts": (state.get("knowledge") or {}).get("drafts", []),
        "risks": (state.get("risk") or {}).get("risks", []),
        "reviews": (state.get("review") or {}).get("reviews", []),
        "proposals": [stage for stage in pipeline if stage.get("name") == "patch" and stage.get("proposals")],
        "injection_previews": (state.get("injection") or {}).get("previews", []),
        "summary": {
            stage.get("name"): stage.get("status")
            for stage in pipeline
        },
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"run_{timestamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, output_path
