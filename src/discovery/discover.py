from __future__ import annotations

"""Discovery orchestration: graph -> scores -> signals -> evidence ->
candidate drafts (status ``proposed``) + owner questions, written as a
reviewable JSON report.

The report is the only output of v1: nothing is written into any registry, and
no LLM is called. Candidate drafts are rule-based and clearly marked as
unconfirmed (``confidence: null``).
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git import Repo

from src.discovery.depgraph import ModuleGraph, build_graph
from src.discovery.evidence import MAX_COMMITS_PER_SYMBOL, module_history, symbol_history
from src.discovery.scoring import score_modules
from src.discovery.signals import Signal, detect_signals
from src.registry.schema import unit_patterns

__all__ = ["QUESTION_TEMPLATES", "SIGNAL_LABELS", "run_discovery"]

SIGNAL_LABELS: dict[str, tuple[str, str]] = {
    "magic_number": ("魔法数字/硬编码阈值", "magic number / hardcoded threshold"),
    "global_instance": ("模块级全局实例", "module-level global instance"),
    "bridge_compat": ("兼容/桥接层", "compatibility / bridge layer"),
    "long_function": ("超长函数", "long function"),
    "long_class": ("超长类", "long class"),
    "dependency_cycle": ("循环依赖", "dependency cycle"),
    "reverted_history": ("频繁回滚历史", "reverted history"),
}

QUESTION_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "magic_number": [
        ("这个数值来自业务规则、协议还是经验阈值？", "Is this value a business rule, a protocol constant, or an experience threshold?"),
        ("修改它会破坏什么历史行为？", "What historical behavior would changing it break?"),
    ],
    "global_instance": [
        ("为什么这里必须使用全局单例/全局实例？", "Why must this be a global singleton/instance?"),
        ("它的生命周期、线程安全与测试隔离如何保证？", "How are its lifecycle, thread safety, and test isolation guaranteed?"),
    ],
    "bridge_compat": [
        ("该桥接/兼容层是否用于兼容历史版本？", "Does this bridge/compat layer exist for backward compatibility?"),
        ("删除它会影响哪些历史场景？", "Which historical scenarios would break if it were removed?"),
    ],
    "long_function": [
        ("为什么这段逻辑长期保持超长且未拆分？", "Why has this logic stayed this long without being split?"),
        ("它是否承载了隐含的执行顺序或状态约束？", "Does it carry implicit ordering or state constraints?"),
    ],
    "long_class": [
        ("这个类的职责边界是什么，为什么长期未拆分？", "What is this class's responsibility boundary, and why has it stayed unsplit?"),
    ],
    "dependency_cycle": [
        ("这个循环依赖是历史包袱还是设计意图？", "Is this dependency cycle historical baggage or intentional design?"),
        ("修改这个模块时需要注意什么初始化顺序？", "What initialization order must be respected when modifying this module?"),
    ],
    "reverted_history": [
        ("这里是否发生过线上事故或回滚？", "Did an incident or rollback happen here?"),
        ("当时回滚的原因是什么，现在是否仍然成立？", "Why was it reverted, and is that reason still valid?"),
    ],
}


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
) -> dict[str, Any]:
    label_zh, label_en = SIGNAL_LABELS[signal.kind]
    candidate_id = f"cand_{module.replace('.', '_')}_{index:03d}"
    evidence_ids = ", ".join(item["short_id"] for item in evidence) or "无"
    return {
        "id": candidate_id,
        "title": f"{label_zh} / {label_en}: {module}（待人工确认 / pending review）",
        "summary": signal.detail,
        "rationale": (
            f"（推断，待人工确认 / inferred, pending human confirmation）"
            f"证据提交：{evidence_ids}"
        ),
        "scope": {"files": [path], "symbols": signal.symbols or []},
        "evidence": evidence,
        "confidence": None,
        "owner": None,
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
        if is_git:
            module_evidence = module_history(root, path)
        top_entry: dict[str, Any] = {
            "module": module,
            "path": path,
            "score": entry["score"],
            "factors": entry["factors"],
            "signals": [signal.to_dict() for signal in module_signals],
            "evidence": module_evidence,
            "existing_unit": existing_units.get(path),
        }
        top_modules.append(top_entry)

        for signal in module_signals:
            evidence_batches: list[list[dict[str, Any]]] = [module_evidence]
            for symbol in (signal.symbols or [])[:2]:
                if is_git:
                    evidence_batches.append(symbol_history(root, path, symbol))
            evidence = _merge_evidence(evidence_batches, cap=MAX_COMMITS_PER_SYMBOL + 3)
            candidate = _build_candidate(module, path, signal, evidence, head_sha, index)
            questions = [
                {"zh": zh, "en": en} for zh, en in QUESTION_TEMPLATES.get(signal.kind, [])
            ]
            candidate["questions"] = questions
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
