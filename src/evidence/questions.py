from __future__ import annotations

"""Owner questions for knowledge candidates.

Questions are generated from signal-kind templates (bilingual zh/en) so a
maintainer can confirm or correct an inferred candidate. When a candidate has
too little traceable evidence, generic insufficiency questions are prepended —
mirroring the design document's "evidence insufficient -> ask the owner" loop.

v1 deliberately has no platform notification: questions live in a local JSON
file and answers come back through ``kc ask-owner answer``.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.evidence.confidence import is_sufficiently_evidenced

__all__ = [
    "INSUFFICIENCY_QUESTIONS",
    "QUESTION_TEMPLATES",
    "SIGNAL_LABELS",
    "QuestionDocError",
    "add_answer",
    "build_candidate_questions",
    "generate_questions",
    "write_questions",
]

#: Human-readable signal-kind labels used in candidate titles.
SIGNAL_LABELS: dict[str, tuple[str, str]] = {
    "magic_number": ("魔法数字/硬编码阈值", "magic number / hardcoded threshold"),
    "global_instance": ("模块级全局实例", "module-level global instance"),
    "bridge_compat": ("兼容/桥接层", "compatibility / bridge layer"),
    "long_function": ("超长函数", "long function"),
    "long_class": ("超长类", "long class"),
    "dependency_cycle": ("循环依赖", "dependency cycle"),
    "reverted_history": ("频繁回滚历史", "reverted history"),
}

#: Per-signal-kind questions, each as a (zh, en) pair.
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

#: Extra questions asked when a candidate lacks enough traceable evidence.
INSUFFICIENCY_QUESTIONS: list[tuple[str, str]] = [
    (
        "目前证据链不足，能否补充出处（Commit/MR/Issue/事故记录）？",
        "The evidence chain is thin — can you add sources (commit/MR/issue/incident records)?",
    ),
    (
        "这条知识由谁负责维护？请确认或更正负责人。",
        "Who owns this knowledge? Please confirm or correct the owner.",
    ),
]


class QuestionDocError(ValueError):
    """Raised when a questions document is malformed for the requested action."""


def build_candidate_questions(
    signal_kind: str,
    evidence: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Questions for one candidate: kind-specific, plus insufficiency extras."""
    questions: list[dict[str, str]] = []
    if not is_sufficiently_evidenced(evidence):
        questions.extend({"zh": zh, "en": en} for zh, en in INSUFFICIENCY_QUESTIONS)
    questions.extend({"zh": zh, "en": en} for zh, en in QUESTION_TEMPLATES.get(signal_kind, []))
    return questions


def generate_questions(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the per-candidate question entries of a questions document."""
    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        entries.append(
            {
                "candidate_id": candidate.get("id", ""),
                "module": candidate.get("scope", {}).get("files", [""])[0] if isinstance(candidate.get("scope"), dict) else "",
                "title": candidate.get("title", ""),
                "evidence_count": len(candidate.get("evidence") or []),
                "insufficient": not is_sufficiently_evidenced(candidate.get("evidence")),
                "items": build_candidate_questions(
                    _signal_kind_of(candidate),
                    candidate.get("evidence"),
                ),
                "answers": [],
            }
        )
    return entries


def _signal_kind_of(candidate: dict[str, Any]) -> str:
    """Best-effort signal kind of a candidate (used to pick question templates).

    Unknown kinds yield an empty string, in which case only the generic
    (insufficiency) questions apply — never a guess.
    """
    explicit = candidate.get("signal_kind")
    if explicit in QUESTION_TEMPLATES:
        return str(explicit)
    title = candidate.get("title", "")
    for kind in QUESTION_TEMPLATES:
        label_zh, label_en = SIGNAL_LABELS.get(kind, (kind, kind))
        if label_zh in title or label_en in title:
            return kind
    return ""


def write_questions(
    candidates: list[dict[str, Any]],
    repo: str | Path,
    out_dir: str | Path,
) -> tuple[dict[str, Any], Path]:
    """Write ``questions_<timestamp>.json``. Returns (document, path)."""
    output_dir = Path(out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repo": str(Path(repo).resolve()),
        "questions": generate_questions(candidates),
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"questions_{timestamp}.json"
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return document, output_path


def add_answer(
    document: dict[str, Any],
    candidate_id: str,
    answer: str,
    owner: str | None = None,
) -> dict[str, Any]:
    """Append a human answer to one candidate's question entry (in place).

    Returns the updated document. Raises QuestionDocError for unknown ids.
    """
    for entry in document.get("questions", []):
        if entry.get("candidate_id") == candidate_id:
            record = {
                "answer": answer,
                "owner": owner,
                "answered_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
            entry.setdefault("answers", []).append(record)
            return document
    raise QuestionDocError(f"Unknown candidate id in questions document: {candidate_id}")


def save_questions_document(document: dict[str, Any], path: str | Path) -> Path:
    """Write a (possibly updated) questions document back to disk."""
    target = Path(path)
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
