from __future__ import annotations

"""Webhook event pipeline: turn a normalized event into Knowledge CI actions.

Everything is write-only into the project's report/patch directories — the
registry and knowledge text are never modified here, so an event can only
produce artifacts for human review.
"""

from pathlib import Path
from typing import Any

from src.config import load_project_paths, load_settings
from src.freshness.check import run_freshness

__all__ = ["DEFAULT_EVENT_ACTIONS", "run_event_actions"]

#: Default actions per event kind. All are read-only regarding knowledge.
DEFAULT_EVENT_ACTIONS: dict[str, list[str]] = {
    "push": ["analyze", "freshness", "discover"],
    "mr": ["analyze", "freshness", "discover"],
}


def _action_analyze(event: dict[str, Any], paths: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    from src.impact.analyzer import analyze_commit, write_report

    report = analyze_commit(
        commit_hash=event["head_sha"],
        project_path=paths["project_root"],
        registry_path=paths["registry_path"],
    )
    output_path = write_report(report, paths["reports_path"])
    return {
        "name": "analyze",
        "ok": True,
        "detail": f"impact report: {output_path.name}",
        "affected_units": report.get("affected_units", []),
        "unmanaged_files": len(report.get("unmanaged_files", [])),
    }


def _action_freshness(event: dict[str, Any], paths: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "name": "freshness",
        "ok": True,
        "detail": f"freshness report: {output_path.name}",
        "summary": summary,
    }


def _action_discover(event: dict[str, Any], paths: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    from src.discovery.discover import run_discovery

    report, output_path = run_discovery(
        repo_root=paths["project_root"],
        settings=settings,
        out_dir=paths["reports_path"],
        registry_path=paths["registry_path"],
    )
    return {
        "name": "discover",
        "ok": True,
        "detail": f"discovery report: {output_path.name}",
        "candidates": report.get("candidate_count", 0),
    }


_ACTIONS: dict[str, Any] = {
    "analyze": _action_analyze,
    "freshness": _action_freshness,
    "discover": _action_discover,
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
    for action_name in action_names:
        action = _ACTIONS.get(action_name)
        if action is None:
            results.append({"name": action_name, "ok": False, "detail": "unknown action"})
            continue
        try:
            result = action(event, paths, settings)
        except Exception as error:  # noqa: BLE001 - the server must survive bad events
            result = {
                "name": action_name,
                "ok": False,
                "detail": f"{type(error).__name__}: {error}",
            }
        results.append(result)
    return results
