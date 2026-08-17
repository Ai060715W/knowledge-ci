from __future__ import annotations

"""Discovery orchestration: graph -> scores -> signals -> evidence ->
candidate drafts (status ``proposed``) + owner questions, written as a
reviewable JSON report.

The report is the only output of discovery: nothing is written into any
registry, and no LLM is called. Candidate drafts are rule-based, carry a
computed confidence (``src/evidence/confidence.py``), and clearly mark
inferred owners as suggestions (``owner_inferred: true``).
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git import Repo

from src.discovery.depgraph import ModuleGraph, build_graph
from src.discovery.evidence import (
    MAX_COMMITS_PER_SYMBOL,
    infer_owners,
    module_history,
    symbol_history,
)
from src.discovery.scoring import score_modules
from src.discovery.signals import Signal, detect_signals
from src.evidence.confidence import compute_confidence
from src.evidence.questions import SIGNAL_LABELS, build_candidate_questions
from src.registry.schema import unit_patterns

__all__ = ["run_discovery"]

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


def _merge_evidence(items: list[list[dict[str, Any]]], cap: int = 8) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in items:
        for item in batch:
            if item["id"] not in seen:
                seen.add(item["id"])
                merged.append(item)
            if len(merged) >= cap:
                return merged
    return merged


def _build_candidate(
    module: str,
    path: str,
    signal: Signal,
    evidence: list[dict[str, Any]],
    head_sha: str | None,
    index: int,
    settings: dict[str, Any] | None,
    owners: dict[str, Any],
) -> dict[str, Any]:
    label_zh, label_en = SIGNAL_LABELS[signal.kind]
    candidate_id = f"cand_{module.replace('.', '_')}_{index:03d}"
    evidence_ids = ", ".join(item["short_id"] for item in evidence) or "无"
    suggested_owner = (owners.get("suggested") or [None])[0]
    return {
        "id": candidate_id,
        "signal_kind": signal.kind,
        "title": f"{label_zh} / {label_en}: {module}（待人工确认 / pending review）",
        "summary": signal.detail,
        "rationale": (
            f"（推断，待人工确认 / inferred, pending human confirmation）"
            f"证据提交：{evidence_ids}"
        ),
        "scope": {"files": [path], "symbols": signal.symbols or []},
        "evidence": evidence,
        "confidence": compute_confidence(evidence, settings),
        "owner": suggested_owner,
        "owner_inferred": suggested_owner is not None,
        "reviewer": None,
        "status": "proposed",
        "knowledge_delta": {"ops": [{"insert": signal.detail}]},
        "last_verified": None,
        "code_hash": (head_sha or "")[:8],
        "version": 1,
    }


def run_discovery(
    repo_root: str | Path,
    settings: dict[str, Any] | None = None,
    top_k: int | None = None,
    out_dir: str | Path = ".",
    registry_path: str | Path | None = None,
    use_cache: bool = True,
    max_signals_per_module: int = 3,
) -> tuple[dict[str, Any], Path]:
    """Run discovery and write ``discovery_<timestamp>.json``. Returns (report, path).

    Graceful degradation: a directory without Python files (or without git)
    still produces a report explaining what was skipped.
    """
    root = Path(repo_root).resolve()
    output_dir = Path(out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    discovery = (settings or {}).get("discovery") or {}
    resolved_top_k = top_k or int(discovery.get("top_k", 10))
    long_span_lines = int(discovery.get("long_span_lines", 80))
    exclude_paths = [str(item) for item in (discovery.get("exclude_paths") or [])]

    head_sha = _head_commit(root)
    # The cache key includes the exclusion list so different exclude settings
    # never reuse each other's graphs.
    exclude_digest = hashlib.sha1(
        json.dumps(exclude_paths, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    cache_dir = output_dir / "cache"
    graph: ModuleGraph | None = None
    cache_hit = False
    if use_cache and head_sha:
        cache_file = cache_dir / f"depgraph_{head_sha[:12]}_{exclude_digest}.json"
        graph = ModuleGraph.load(cache_file)
        cache_hit = graph is not None
    if graph is None:
        graph = build_graph(root, exclude_paths=exclude_paths)
        if use_cache and head_sha:
            cache_dir.mkdir(parents=True, exist_ok=True)
            graph.save(cache_dir / f"depgraph_{head_sha[:12]}_{exclude_digest}.json")

    existing_units: dict[str, str] = {}
    if registry_path:
        try:
            for unit in _load_registry_units(registry_path):
                unit_id = unit.get("id")
                if not unit_id:
                    continue
                for pattern in unit_patterns(unit):
                    existing_units.setdefault(str(pattern), str(unit_id))
        except (OSError, ValueError):
            pass

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if not graph.nodes:
        report = {
            "generated_at": generated_at,
            "repo": str(root),
            "head_commit": head_sha,
            "language": "python",
            "modules_scanned": 0,
            "parse_errors": graph.parse_errors,
            "cache_hit": cache_hit,
            "top_modules": [],
            "candidates": [],
            "note": "No Python source files found in this repository; discovery produced no candidates.",
        }
        output_path = _write_report(report, output_dir)
        return report, output_path

    scores, stats, is_git = score_modules(root, graph, settings, top_k=resolved_top_k)
    signals = detect_signals(
        root,
        graph,
        stats if is_git else None,
        long_span_lines=long_span_lines,
        exclude_paths=exclude_paths,
    )

    candidates: list[dict[str, Any]] = []
    top_modules: list[dict[str, Any]] = []
    index = 1
    for entry in scores:
        module = entry["module"]
        path = entry["path"]
        module_signals = signals.get(module, [])[:max_signals_per_module]
        module_evidence: list[dict[str, Any]] = []
        owners: dict[str, Any] = {
            "codeowners": [],
            "blame_authors": [],
            "suggested": [],
            "inferred": False,
        }
        if is_git:
            module_evidence = module_history(root, path)
            owners = infer_owners(root, path)
        top_entry: dict[str, Any] = {
            "module": module,
            "path": path,
            "score": entry["score"],
            "factors": entry["factors"],
            "signals": [signal.to_dict() for signal in module_signals],
            "evidence": module_evidence,
            "owners": owners,
            "existing_unit": existing_units.get(path),
        }
        top_modules.append(top_entry)

        for signal in module_signals:
            evidence_batches: list[list[dict[str, Any]]] = [module_evidence]
            for symbol in (signal.symbols or [])[:2]:
                if is_git:
                    evidence_batches.append(symbol_history(root, path, symbol))
            evidence = _merge_evidence(evidence_batches, cap=MAX_COMMITS_PER_SYMBOL + 3)
            candidate = _build_candidate(
                module, path, signal, evidence, head_sha, index, settings, owners
            )
            candidate["questions"] = build_candidate_questions(signal.kind, evidence)
            candidates.append(candidate)
            index += 1

    report = {
        "generated_at": generated_at,
        "repo": str(root),
        "head_commit": head_sha,
        "language": "python",
        "git": is_git,
        "cache_hit": cache_hit,
        "modules_scanned": len(graph.nodes),
        "parse_errors": graph.parse_errors,
        "top_modules": top_modules,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "question_count": sum(len(candidate.get("questions", [])) for candidate in candidates),
    }
    output_path = _write_report(report, output_dir)
    return report, output_path


def _load_registry_units(registry_path: str | Path) -> list[dict[str, Any]]:
    with Path(registry_path).open("r", encoding="utf-8") as registry_file:
        registry = json.load(registry_file)
    return registry.get("units", [])


def _write_report(report: dict[str, Any], output_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"discovery_{timestamp}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_path
