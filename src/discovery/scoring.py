from __future__ import annotations

"""Module value scoring from git history + the dependency graph.

Implements the design document formula::

    ModuleScore = α·change_frequency   + β·dependency_centrality
                + γ·incident_history   + δ·rollback_count
                + ε·contributor_entropy + ζ·cross_layer_impact

Every factor is min-max normalized over all modules (so each lies in [0, 1]),
then weighted with the ``discovery.weights`` config values. History factors are
computed from a single ``git log`` pass, so the cost does not scale with the
number of modules times the number of commits.
"""

import math
from pathlib import Path
from typing import Any

from git import Repo

from src.discovery.depgraph import ModuleGraph

__all__ = ["DEFAULT_INCIDENT_KEYWORDS", "REVERT_PATTERNS", "collect_commit_stats", "compute_scores", "score_modules"]

#: Commit subjects containing these substrings count as reverts
#: (case-insensitive, plain substring matching).
REVERT_PATTERNS: tuple[str, ...] = ("revert", "rollback", "roll back")

#: Commit subjects matching these keywords count as incident/fix history.
DEFAULT_INCIDENT_KEYWORDS: tuple[str, ...] = (
    "incident",
    "hotfix",
    "crash",
    "outage",
    "deadlock",
    "race condition",
    "data loss",
    "security",
    "cve",
    "vulnerability",
)


def _factor_defaults() -> dict[str, float]:
    return {
        "change_frequency": 1.0,
        "dependency_centrality": 1.0,
        "incident_history": 1.0,
        "rollback_count": 1.0,
        "contributor_entropy": 1.0,
        "cross_layer_impact": 1.0,
    }


def _weights_from_settings(settings: dict[str, Any]) -> dict[str, float]:
    raw = (settings or {}).get("discovery", {}).get("weights") or {}
    weights = _factor_defaults()
    for key in weights:
        if key in raw:
            try:
                weights[key] = float(raw[key])
            except (TypeError, ValueError):
                continue
    return weights


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    minimum = min(values.values())
    maximum = max(values.values())
    span = maximum - minimum
    if span == 0:
        return {key: 0.0 for key in values}
    return {key: (value - minimum) / span for key, value in values.items()}


def _git_log_single_pass(repo_path: Path) -> tuple[list[dict[str, Any]], bool]:
    """One ``git log --all --name-only`` pass -> [{hash, author, subject, paths}].

    Returns (commits, ok). ``ok`` is False when the directory is not a git
    repository; callers then degrade gracefully.
    """
    repo = None
    try:
        repo = Repo(repo_path)
        output = repo.git.log(
            "--all",
            "--name-only",
            "--format=%x00%H%x00%an%x00%ae%x00%s",
        )
    except Exception:
        return [], False
    finally:
        if repo is not None:
            try:
                repo.close()
            except Exception:
                pass

    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in output.splitlines():
        if raw_line.startswith("\x00"):
            parts = raw_line.lstrip("\x00").split("\x00")
            if len(parts) >= 4:
                current = {
                    "hash": parts[0],
                    "author_name": parts[1],
                    "author_email": parts[2],
                    "subject": parts[3],
                    "paths": [],
                }
                commits.append(current)
            continue
        line = raw_line.strip()
        if line and current is not None:
            current["paths"].append(line)
    return commits, True


def collect_commit_stats(
    repo_path: str | Path,
    graph: ModuleGraph,
    incident_keywords: tuple[str, ...] = DEFAULT_INCIDENT_KEYWORDS,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Aggregate per-module git history statistics.

    Returns (stats, is_git) where ``stats`` maps a module to
    {"commits": n, "reverts": n, "incidents": n, "authors": {email: count},
    "introduced_at": "<hash>"} and ``is_git`` is False when the directory is
    not a git repository.
    """
    root = Path(repo_path)
    commits, is_git = _git_log_single_pass(root)
    stats: dict[str, dict[str, Any]] = {}

    def module_stats(module: str) -> dict[str, Any]:
        return stats.setdefault(
            module,
            {"commits": 0, "reverts": 0, "incidents": 0, "authors": {}, "introduced_at": None},
        )

    if not is_git:
        return stats, False

    # Map each source path to the module (or package) it belongs to.
    path_to_module: dict[str, str] = {}
    for node in graph.nodes.values():
        path_to_module[node.path] = node.module

    for commit in commits:
        subject_lower = commit["subject"].lower()
        is_revert = any(keyword in subject_lower for keyword in REVERT_PATTERNS)
        is_incident = any(keyword in subject_lower for keyword in incident_keywords)
        touched: set[str] = set()
        for path in commit["paths"]:
            module = path_to_module.get(path)
            if module:
                touched.add(module)
        for module in touched:
            entry = module_stats(module)
            entry["commits"] += 1
            if is_revert:
                entry["reverts"] += 1
            if is_incident:
                entry["incidents"] += 1
            author_key = commit["author_email"] or commit["author_name"]
            entry["authors"][author_key] = entry["authors"].get(author_key, 0) + 1

    # Introduced-at: oldest commit touching each module (commits are newest-first).
    for commit in reversed(commits):
        for path in commit["paths"]:
            module = path_to_module.get(path)
            if module and stats[module]["introduced_at"] is None:
                stats[module]["introduced_at"] = commit["hash"]
    return stats, True


def compute_scores(
    graph: ModuleGraph,
    stats: dict[str, dict[str, Any]],
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return modules with their normalized factors and weighted scores.

    Result is sorted by score, descending::

        [{"module", "path", "score", "factors": {...}}]
    """
    weights = _weights_from_settings(settings)
    raw: dict[str, dict[str, float]] = {name: {} for name in graph.nodes}

    for name, node in graph.nodes.items():
        entry = stats.get(name, {})
        authors = entry.get("authors") or {}
        total_author_commits = sum(authors.values())
        entropy = 0.0
        if total_author_commits and len(authors) > 1:
            for count in authors.values():
                probability = count / total_author_commits
                entropy -= probability * math.log(probability)
            entropy /= math.log(len(authors))
        raw[name] = {
            "change_frequency": float(entry.get("commits", 0)),
            "dependency_centrality": graph.degree_centrality(name),
            "incident_history": float(entry.get("incidents", 0)),
            "rollback_count": float(entry.get("reverts", 0)),
            "contributor_entropy": entropy,
            "cross_layer_impact": graph.cross_layer_impact(name),
        }

    normalized = {factor: _normalize({name: values[factor] for name, values in raw.items()}) for factor in _factor_defaults()}

    results: list[dict[str, Any]] = []
    for name in graph.nodes:
        factors = {factor: round(normalized[factor][name], 4) for factor in _factor_defaults()}
        score = sum(weights[factor] * factors[factor] for factor in _factor_defaults())
        results.append(
            {
                "module": name,
                "path": graph.nodes[name].path,
                "score": round(score, 4),
                "factors": factors,
            }
        )
    results.sort(key=lambda item: (-item["score"], item["module"]))
    return results


def score_modules(
    repo_path: str | Path,
    graph: ModuleGraph,
    settings: dict[str, Any] | None = None,
    top_k: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], bool]:
    """Convenience wrapper: stats + scores. Returns (scores, stats, is_git)."""
    root = Path(repo_path)
    incident_keywords = DEFAULT_INCIDENT_KEYWORDS
    discovery = (settings or {}).get("discovery") or {}
    if isinstance(discovery.get("incident_keywords"), list):
        incident_keywords = tuple(str(item) for item in discovery["incident_keywords"])
    stats, is_git = collect_commit_stats(root, graph, incident_keywords=incident_keywords)
    scores = compute_scores(graph, stats, settings)
    return scores[:top_k], stats, is_git
