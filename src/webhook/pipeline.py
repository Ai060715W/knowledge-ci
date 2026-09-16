from __future__ import annotations

"""Webhook event pipeline: turn a normalized event into Knowledge CI actions.

Everything is write-only into the project's report/patch directories — the
registry and knowledge text are never modified here, so an event can only
produce artifacts for human review.
"""

import json
from pathlib import Path
from typing import Any

from src.config import load_project_paths, load_settings
from src.freshness.check import run_freshness
from src.patch.pr_manager import post_pr_comment, preview_url

__all__ = ["DEFAULT_EVENT_ACTIONS", "run_event_actions"]

#: Default actions per event kind. All are read-only regarding knowledge.
DEFAULT_EVENT_ACTIONS: dict[str, list[str]] = {
    "push": ["analyze", "freshness", "discover"],
    "mr": ["analyze", "freshness", "discover", "comment"],
}


def _action_analyze(
    event: dict[str, Any],
    paths: dict[str, Any],
    settings: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    from src.impact.analyzer import analyze_commit, write_report

    report = analyze_commit(
        commit_hash=event["head_sha"],
        project_path=paths["project_root"],
        registry_path=paths["registry_path"],
    )
    output_path = write_report(report, paths["reports_path"])
    context["impact_report"] = report
    context["impact_report_path"] = output_path
    return {
        "name": "analyze",
        "ok": True,
        "detail": f"impact report: {output_path.name}",
        "affected_units": report.get("affected_units", []),
        "unmanaged_files": len(report.get("unmanaged_files", [])),
    }


def _action_freshness(
    event: dict[str, Any],
    paths: dict[str, Any],
    settings: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    webhook_settings = settings.get("webhook", {})
    report, output_path = run_freshness(
        repo_root=paths["project_root"],
        registry_path=paths["registry_path"],
        settings=settings,
        out_dir=paths["reports_path"],
        apply=False,  # events never mutate the registry
        auto_patch=bool(webhook_settings.get("auto_patch", False)),
        patches_path=paths["patches_path"],
    )
    summary = report.get("summary", {})
    context["freshness_report"] = report
    context["freshness_report_path"] = output_path
    return {
        "name": "freshness",
        "ok": True,
        "detail": f"freshness report: {output_path.name}",
        "summary": summary,
    }


def _action_discover(
    event: dict[str, Any],
    paths: dict[str, Any],
    settings: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    from src.discovery.discover import run_discovery

    report, output_path = run_discovery(
        repo_root=paths["project_root"],
        settings=settings,
        out_dir=paths["reports_path"],
        registry_path=paths["registry_path"],
    )
    context["discovery_report"] = report
    context["discovery_report_path"] = output_path
    return {
        "name": "discover",
        "ok": True,
        "detail": f"discovery report: {output_path.name}",
        "candidates": report.get("candidate_count", 0),
    }


def _cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _impact_rows(report: dict[str, Any]) -> list[str]:
    rows = ["| Unit | Changed files | Symbols |", "|:---|:---|:---|"]
    grouped: dict[str, dict[str, set[str]]] = {}
    for item in report.get("changed_files", []):
        unit_id = item.get("unit_id") or "(unmanaged)"
        entry = grouped.setdefault(unit_id, {"files": set(), "symbols": set()})
        entry["files"].add(str(item.get("path", "")))
        summary = item.get("summary", {})
        for key in ("functions", "classes", "constants"):
            entry["symbols"].update(str(value) for value in summary.get(key, []) if value)
    if not grouped:
        rows.append("| none | none | none |")
        return rows
    for unit_id in sorted(grouped):
        files = ", ".join(sorted(grouped[unit_id]["files"])) or "none"
        symbols = ", ".join(sorted(grouped[unit_id]["symbols"])) or "none"
        rows.append(f"| {_cell(unit_id)} | {_cell(files)} | {_cell(symbols)} |")
    return rows


def _freshness_rows(report: dict[str, Any]) -> list[str]:
    rows = ["| Unit | Verdict | Basis | Layers | Actions |", "|:---|:---|:---|:---|:---|"]
    units = report.get("units", [])
    if not units:
        rows.append("| none | none | none | none | none |")
        return rows
    for unit in units:
        layers = ", ".join(str(layer.get("layer", "")) for layer in unit.get("layers", []) if layer.get("layer"))
        actions = ", ".join(str(action) for action in unit.get("actions", [])) or "none"
        rows.append(
            f"| {_cell(unit.get('unit_id'))} | {_cell(unit.get('verdict'))} | "
            f"{_cell(unit.get('basis'))} | {_cell(layers or 'none')} | {_cell(actions)} |"
        )
    return rows


def _patch_preview_rows(paths: dict[str, Any], freshness_report: dict[str, Any], base_url: str) -> list[str]:
    rows = ["| Patch | Unit | Preview | Local file |", "|:---|:---|:---|:---|"]
    patch_names: list[str] = []
    for unit in freshness_report.get("units", []):
        for action in unit.get("actions", []):
            if isinstance(action, str) and action.startswith("patch_written:"):
                patch_names.append(action.split(":", 1)[1].strip())
    patch_files = [Path(paths["patches_path"]) / name for name in sorted(set(patch_names))]
    existing = [path for path in patch_files if path.is_file()]
    if not existing:
        rows.append("| none | none | none | none |")
        return rows

    for patch_path in existing:
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
        rows.append(
            f"| {_cell(patch.get('patch_id', patch_path.stem))} | {_cell(patch.get('unit_id'))} | "
            f"{_cell(preview_url(patch, base_url))} | {_cell(patch_path.name)} |"
        )
    return rows


def build_mr_comment(
    event: dict[str, Any],
    paths: dict[str, Any],
    context: dict[str, Any],
    preview_base_url: str = "http://localhost:8080/",
) -> str:
    impact_report = context.get("impact_report") or {}
    freshness_report = context.get("freshness_report") or {}
    summary = freshness_report.get("summary", {})
    summary_text = ", ".join(f"{key}={value}" for key, value in sorted(summary.items())) or "none"
    pr_number = event.get("number", "unknown")
    title = event.get("title") or event.get("action") or "MR update"

    lines = [
        "## Knowledge CI MR Summary",
        "",
        f"- PR/MR: #{pr_number} {title}",
        f"- Head commit: {event.get('head_sha', 'unknown')}",
        f"- Freshness summary: {summary_text}",
        "",
        "### Impacted Knowledge Units",
        *_impact_rows(impact_report),
        "",
        "### Freshness Conclusions",
        *_freshness_rows(freshness_report),
        "",
        "### Patch Previews",
        *_patch_preview_rows(paths, freshness_report, preview_base_url),
        "",
        "> Knowledge CI only produced reports and PENDING previews. Nothing was landed automatically.",
    ]
    return "\n".join(lines)


def _action_comment(
    event: dict[str, Any],
    paths: dict[str, Any],
    settings: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if event.get("kind") != "mr":
        return {"name": "comment", "ok": True, "detail": "skipped: not an MR event"}
    if "number" not in event:
        return {"name": "comment", "ok": False, "detail": "missing MR number"}

    webhook_settings = settings.get("webhook", {})
    body = build_mr_comment(
        event,
        paths,
        context,
        preview_base_url=str(webhook_settings.get("preview_base_url", "http://localhost:8080/")),
    )
    result = post_pr_comment(
        repo_path=paths["project_root"],
        pr_number=event["number"],
        body=body,
        reports_path=paths["reports_path"],
        dry_run=bool(webhook_settings.get("comment_dry_run", True)),
    )
    return {
        "name": "comment",
        "ok": True,
        "detail": result["detail"],
        "published": result["published"],
        "local_path": result["local_path"],
    }


_ACTIONS: dict[str, Any] = {
    "analyze": _action_analyze,
    "freshness": _action_freshness,
    "discover": _action_discover,
    "comment": _action_comment,
}


def run_event_actions(
    event: dict[str, Any],
    repo_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute the configured actions for one normalized event.

    ``repo_info`` must carry ``config_path`` (the local checkout's
    ``.knowledge-ci/config.yaml``). Failures are captured per action and
    reported instead of crashing the server.
    """
    config_path = Path(repo_info["config_path"])
    paths = load_project_paths(config_path)
    settings = load_settings(config_path)
    webhook_settings = settings.get("webhook", {})
    action_names = list(
        webhook_settings.get("events", DEFAULT_EVENT_ACTIONS).get(
            event.get("kind", "push"), DEFAULT_EVENT_ACTIONS["push"]
        )
    )

    results: list[dict[str, Any]] = []
    context: dict[str, Any] = {}
    for action_name in action_names:
        action = _ACTIONS.get(action_name)
        if action is None:
            results.append({"name": action_name, "ok": False, "detail": "unknown action"})
            continue
        try:
            result = action(event, paths, settings, context)
        except Exception as error:  # noqa: BLE001 - the server must survive bad events
            result = {
                "name": action_name,
                "ok": False,
                "detail": f"{type(error).__name__}: {error}",
            }
        results.append(result)
    return results
