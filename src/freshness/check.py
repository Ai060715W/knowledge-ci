from __future__ import annotations

"""Freshness orchestration: run the four-layer funnel over a registry and
write an explainable ``freshness_<ts>.json`` report.

Default mode is read-only. ``apply`` performs only safe bookkeeping: refresh
``last_verified``/``code_hash`` for confirmed-fresh units and move
``outdated`` units through the schema v2 state machine. ``auto_patch`` turns
``partial_update`` verdicts into PENDING patch files — human review still
applies before anything lands.
"""

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from git import NULL_TREE, Repo

from src.config import CONFIG_DEFAULTS
from src.discovery.depgraph import ModuleGraph, build_graph
from src.evidence.confidence import compute_confidence
from src.evidence.questions import build_candidate_questions
from src.freshness.layers import (
    ast_semantic_filter,
    changed_symbols,
    impact_analysis,
    layer_time,
)
from src.freshness.llm import judge_freshness
from src.patch.delta import DeltaValidationError, apply_delta_ops, delta_to_text
from src.patch.generator import next_patch_id
from src.registry.schema import RegistryValidationError, is_injectable, transition_status
from src.registry.store import RegistryStore

__all__ = ["run_freshness"]

DEFAULT_MODEL = "deepseek-chat"


def _settings_sections(settings: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(CONFIG_DEFAULTS)
    if settings:
        for section, defaults in CONFIG_DEFAULTS.items():
            raw = settings.get(section)
            if isinstance(raw, dict):
                merged[section] = {**defaults, **raw}
    return merged


def _head_commit(repo: Repo) -> str | None:
    try:
        return repo.head.commit.hexsha
    except Exception:
        return None


def _decode_blob(blob: Any | None) -> str:
    if blob is None:
        return ""
    try:
        # utf-8-sig also strips a leading BOM that ast.parse would reject.
        return blob.data_stream.read().decode("utf-8-sig", errors="replace")
    except Exception:
        return ""


def _diff_excerpt(patch_text: str, max_lines: int = 40) -> list[str]:
    excerpt: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith(("@@", "+", "-")) and not line.startswith(("+++", "---")):
            excerpt.append(line)
        if len(excerpt) >= max_lines:
            break
    return excerpt


def _file_content_at(repo: Repo, commit_id: str, path: str) -> str:
    try:
        commit = repo.commit(commit_id)
        blob = commit.tree / path
        return _decode_blob(blob)
    except Exception:
        return ""


def _old_new_for_commit(repo: Repo, commit_id: str, paths: list[str]) -> dict[str, dict[str, str]]:
    """{path: {"old": ..., "new": ...}} for one commit (first parent baseline)."""
    commit = repo.commit(commit_id)
    parent = commit.parents[0] if commit.parents else None
    result: dict[str, dict[str, str]] = {}
    for path in paths:
        old_source = _file_content_at(repo, parent.hexsha, path) if parent is not None else ""
        new_source = _file_content_at(repo, commit_id, path)
        result[path] = {"old": old_source, "new": new_source}
    return result


def _patch_text_for(repo: Repo, commit_id: str, path: str) -> str:
    commit = repo.commit(commit_id)
    try:
        if commit.parents:
            diffs = commit.parents[0].diff(commit, paths=path, create_patch=True)
        else:
            diffs = commit.diff(NULL_TREE, paths=path, create_patch=True)
        for diff in diffs:
            patch = diff.diff or b""
            if isinstance(patch, bytes):
                return patch.decode("utf-8", errors="replace")
            return str(patch)
    except Exception:
        pass
    return ""


def _write_pending_patch(
    unit: dict[str, Any],
    verdict: dict[str, Any],
    patches_path: Path,
    head_sha: str | None,
) -> Path:
    """Write a PENDING patch file for a partial_update verdict."""
    patches_path.mkdir(parents=True, exist_ok=True)
    patch_id = next_patch_id(patches_path)
    old_version = int(unit.get("version", 0))
    patch = {
        "patch_id": patch_id,
        "status": "PENDING",
        "unit_id": unit.get("id", ""),
        "old_version": old_version,
        "new_version": old_version + 1,
        "risk_level": unit.get("risk_level", "LOW"),
        "delta_ops": verdict["patch_ops"],
        "reasoning": f"[freshness/{verdict['verdict']}] {verdict.get('reasoning', '')}",
        "affected_files": list(unit.get("scope", {}).get("files", [])),
        "related_docs": unit.get("related_docs", []),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "model": "freshness",
        "source": "freshness",
        "source_commit": (head_sha or "")[:8],
        "prompt": verdict.get("prompt", ""),
    }
    output_path = patches_path / f"patch_{patch_id}.json"
    output_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _new_knowledge_draft(unit: dict[str, Any], verdict: dict[str, Any], index: int) -> dict[str, Any]:
    knowledge = verdict.get("new_knowledge") or {}
    draft_id = f"fresh_{unit.get('id', 'unit')}_{index:03d}"
    summary = knowledge.get("summary", "")
    evidence = list(unit.get("evidence") or [])
    return {
        "id": draft_id,
        "signal_kind": "",
        "title": knowledge.get("title", summary[:60]),
        "summary": summary,
        "rationale": knowledge.get("rationale", ""),
        "scope": {
            "files": list(unit.get("scope", {}).get("files", [])),
            "symbols": list(knowledge.get("symbols") or []),
        },
        "evidence": evidence,
        "confidence": compute_confidence(evidence),
        "owner": unit.get("owner"),
        "owner_inferred": bool(unit.get("owner_inferred")),
        "reviewer": None,
        "status": "proposed",
        "knowledge_delta": {"ops": [{"insert": summary}]},
        "related_docs": unit.get("related_docs", []),
        "last_verified": None,
        "code_hash": "",
        "version": 1,
        "questions": build_candidate_questions("", evidence),
    }


def _eligible_units(registry: dict[str, Any], include_all: bool) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for unit in registry.get("units", []):
        status = unit.get("status")
        if status == "retired":
            continue
        if include_all or is_injectable(unit):
            units.append(unit)
    return units


def run_freshness(
    repo_root: str | Path,
    registry_path: str | Path,
    settings: dict[str, Any] | None = None,
    out_dir: str | Path = ".",
    include_all: bool = False,
    apply: bool = False,
    auto_patch: bool = False,
    mock_response: str | None = None,
    no_llm: bool = False,
    patches_path: str | Path | None = None,
    use_cache: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Run the four-layer freshness funnel. Returns (report, path)."""
    root = Path(repo_root).resolve()
    output_dir = Path(out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sections = _settings_sections(settings)
    freshness = sections["freshness"]
    time_filter_days = float(freshness.get("time_filter_days", 30))
    indirect_depth = int(freshness.get("indirect_depth", 2))
    llm_max_units = int(freshness.get("llm_max_units", 20))
    model = (settings or {}).get("model") or DEFAULT_MODEL
    exclude_paths = [str(item) for item in (sections["discovery"].get("exclude_paths") or [])]

    store = RegistryStore(registry_path=registry_path)
    registry = store.load_registry()

    repo = None
    head_sha: str | None = None
    try:
        repo = Repo(root)
        head_sha = _head_commit(repo)
    except Exception:
        repo = None

    # Dependency graph with a per-HEAD cache (exclusion-aware).
    graph = ModuleGraph({})
    cache_hit = False
    exclude_digest = hashlib.sha1(
        json.dumps(exclude_paths, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    cache_dir = output_dir / "cache"
    if head_sha and use_cache:
        cache_file = cache_dir / f"depgraph_{head_sha[:12]}_{exclude_digest}.json"
        cached = ModuleGraph.load(cache_file)
        if cached is not None:
            graph = cached
            cache_hit = True
    if not graph.nodes:
        graph = build_graph(root, exclude_paths=exclude_paths)
        if head_sha and use_cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            graph.save(cache_dir / f"depgraph_{head_sha[:12]}_{exclude_digest}.json")

    units = _eligible_units(registry, include_all)
    results: list[dict[str, Any]] = []
    candidate_drafts: list[dict[str, Any]] = []
    touched_registry_units: list[dict[str, Any]] = []
    llm_calls = 0
    new_knowledge_index = 1

    for unit in units:
        unit_id = str(unit.get("id", ""))
        result: dict[str, Any] = {
            "unit_id": unit_id,
            "title": unit.get("title", ""),
            "previous_status": unit.get("status") or "active",
            "verdict": "error",
            "basis": "",
            "layers": [],
            "actions": [],
        }

        try:
            time_verdict = layer_time(root, unit, fallback_days=time_filter_days)
            result["layers"].append(time_verdict.to_dict())
            if time_verdict.files_missing:
                result["verdict"] = "outdated"
                result["basis"] = "files_missing"
            elif time_verdict.fresh:
                result["verdict"] = "still_valid"
                result["basis"] = "time"
            else:
                if repo is None:
                    result["verdict"] = "needs_llm"
                    result["basis"] = "no_git"
                    results.append(result)
                    continue

                semantic_files: dict[str, set[str]] = {}
                ast_entries: list[dict[str, Any]] = []
                diff_excerpts: list[str] = []
                for commit_entry in time_verdict.commits:
                    commit_id = commit_entry["id"]
                    commit_paths = [
                        path
                        for path in commit_entry.get("paths", [])
                        if path in (unit.get("scope", {}).get("files") or [])
                    ]
                    if not commit_paths:
                        continue
                    sources = _old_new_for_commit(repo, commit_id, commit_paths)
                    old_sources = {path: item["old"] for path, item in sources.items()}
                    new_sources = {path: item["new"] for path, item in sources.items()}
                    ast_verdict = ast_semantic_filter(old_sources, new_sources)
                    result["layers"].append(ast_verdict.to_dict())
                    ast_entries.extend(ast_verdict.per_file)
                    for path in commit_paths:
                        semantic_files.setdefault(path, set()).update(
                            changed_symbols(old_sources.get(path, ""), new_sources.get(path, ""))
                        )
                        diff_excerpts.extend(_diff_excerpt(_patch_text_for(repo, commit_id, path)))

                if not ast_entries or not any(entry.get("semantic") for entry in ast_entries):
                    result["verdict"] = "still_valid"
                    result["basis"] = "ast_noise"
                else:
                    impact_verdict = impact_analysis(unit, graph, semantic_files, depth=indirect_depth)
                    result["layers"].append(impact_verdict.to_dict())
                    if not impact_verdict.in_scope:
                        result["verdict"] = "still_valid"
                        result["basis"] = "out_of_scope"
                    else:
                        context = {
                            "commits": time_verdict.commits,
                            "ast_summary": ast_entries,
                            "impact_reason": impact_verdict.reason,
                            "diff_excerpts": diff_excerpts,
                        }
                        if no_llm:
                            result["verdict"] = "needs_llm"
                            result["basis"] = "no_llm_flag"
                        elif llm_calls >= llm_max_units:
                            result["verdict"] = "needs_llm"
                            result["basis"] = "llm_budget"
                        elif mock_response is None and not os.environ.get("OPENAI_API_KEY"):
                            result["verdict"] = "needs_llm"
                            result["basis"] = "no_api_key"
                        else:
                            verdict = judge_freshness(unit, context, model, mock_response=mock_response)
                            llm_calls += 1
                            result["verdict"] = verdict["verdict"]
                            result["basis"] = "llm"
                            result["llm"] = {
                                "reasoning": verdict.get("reasoning", ""),
                                "attempts": verdict.get("attempts", 1),
                            }
                            _apply_verdict_actions(
                                unit, verdict, result, head_sha,
                                auto_patch, patches_path, candidate_drafts,
                                new_knowledge_index,
                            )
                            if verdict["verdict"] == "new_knowledge":
                                new_knowledge_index += 1
        except (RegistryValidationError, DeltaValidationError, RuntimeError, ValueError) as error:
            result["verdict"] = "error"
            result["basis"] = type(error).__name__
            result["error"] = str(error)

        results.append(result)
        if apply and result["verdict"] == "still_valid":
            unit["last_verified"] = date.today().isoformat()
            unit["code_hash"] = (head_sha or "")[:8]
            result["actions"].append("refreshed_last_verified")
            touched_registry_units.append(unit)
        elif apply and result["verdict"] == "outdated":
            try:
                transition_status(unit, "outdated")
                result["actions"].append("status->outdated")
                touched_registry_units.append(unit)
            except RegistryValidationError as error:
                result["error"] = str(error)

    if apply and touched_registry_units:
        for unit in touched_registry_units:
            store.upsert_unit(unit)

    if repo is not None:
        try:
            repo.close()
        except Exception:
            pass

    summary = _summarize(results)
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repo": str(root),
        "head_commit": head_sha,
        "registry_path": str(Path(registry_path).resolve()),
        "cache_hit": cache_hit,
        "units": results,
        "candidate_drafts": candidate_drafts,
        # Alias so kc ask-owner can consume new_knowledge drafts directly.
        "candidates": candidate_drafts,
        "summary": summary,
    }
    output_path = _write_report(report, output_dir)
    return report, output_path


def _apply_verdict_actions(
    unit: dict[str, Any],
    verdict: dict[str, Any],
    result: dict[str, Any],
    head_sha: str | None,
    auto_patch: bool,
    patches_path: str | Path | None,
    candidate_drafts: list[dict[str, Any]],
    new_knowledge_index: int,
) -> None:
    if verdict["verdict"] == "partial_update" and auto_patch:
        if not patches_path:
            result["actions"].append("auto_patch_skipped: patches_path not configured")
        else:
            old_text = delta_to_text(unit.get("knowledge_delta"))
            apply_delta_ops(old_text, verdict["patch_ops"])  # validate early
            patch_path = _write_pending_patch(unit, verdict, Path(patches_path), head_sha)
            result["actions"].append(f"patch_written: {patch_path.name}")
    if verdict["verdict"] == "new_knowledge":
        draft = _new_knowledge_draft(unit, verdict, new_knowledge_index)
        candidate_drafts.append(draft)
        result["actions"].append(f"draft: {draft['id']}")


def _summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {verdict: 0 for verdict in (
        "still_valid", "partial_update", "outdated", "new_knowledge", "needs_llm", "error"
    )}
    for result in results:
        verdict = result.get("verdict", "error")
        counts[verdict] = counts.get(verdict, 0) + 1
    counts["total"] = len(results)
    return counts


def _write_report(report: dict[str, Any], output_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"freshness_{timestamp}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_path
