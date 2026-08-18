from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlencode

from src.patch.delta import delta_to_text
from src.registry.matcher import match_unit_record
from src.registry.schema import is_injectable


APPLIED_STATUSES = {"APPLIED", "MERGED"}
DEFAULT_MAX_TOKENS = 500
_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
_FEEDBACK_LOCK = threading.Lock()


def normalize_path(file_path: str) -> str:
    """Normalize platform separators to a POSIX-style relative path."""
    normalized = str(PurePosixPath(file_path.replace("\\", "/")))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def resolve_input_path(file_path: str, project_root: str | Path) -> str:
    """Resolve a user-supplied path to the project-repo-relative form used by registry patterns.

    Accepts absolute paths under the project, paths prefixed with the project
    directory name (e.g. ``myapp/src/foo.py``), and bare repo-relative paths.
    """
    path = Path(file_path)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(Path(project_root).resolve())
        except ValueError:
            pass

    normalized = normalize_path(str(path))

    root_name = Path(project_root).resolve().name
    if root_name and (normalized == root_name or normalized.startswith(root_name + "/")):
        normalized = normalized[len(root_name):].lstrip("/")

    return normalized


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def applied_patches(patches_path: str | Path, unit_id: str) -> list[dict[str, Any]]:
    """Collect patches that have landed for a unit.

    PENDING or REJECTED patches are excluded so that only reviewed knowledge
    enters the injection context.
    """
    patches_dir = Path(patches_path)
    if not patches_dir.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for patch_file in sorted(patches_dir.glob("patch_*.json")):
        try:
            patch = load_json(patch_file)
        except (OSError, json.JSONDecodeError):
            continue
        if patch.get("unit_id") == unit_id and patch.get("status") in APPLIED_STATUSES:
            found.append(patch)
    return found


def history_decisions(unit: dict[str, Any], patches: list[dict[str, Any]]) -> list[str]:
    """Combine explicit history_decisions entries with landed patch reasoning."""
    raw_history = unit.get("history_decisions") or []
    if isinstance(raw_history, str):
        raw_history = [raw_history]
    decisions = [str(item) for item in raw_history]
    for patch in patches:
        reasoning = patch.get("reasoning") or patch.get("patch_id")
        generated = str(patch.get("generated_at") or "")[:10]
        suffix = f"（{patch.get('patch_id')}，{generated}）" if generated else f"（{patch.get('patch_id')}）"
        decisions.append(f"{reasoning}{suffix}")
    return decisions


def related_reports(reports_path: str | Path, unit_id: str) -> list[dict[str, Any]]:
    """Impact reports whose affected_units contain the unit, newest first."""
    reports_dir = Path(reports_path)
    if not reports_dir.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for report_file in sorted(reports_dir.glob("impact_*.json")):
        try:
            report = load_json(report_file)
        except (OSError, json.JSONDecodeError):
            continue
        if unit_id in (report.get("affected_units") or []):
            found.append(report)
    found.sort(key=lambda report: report.get("generated_at") or "", reverse=True)
    return found


def impact_warnings(
    unit_id: str,
    registry: dict[str, Any],
    reports: list[dict[str, Any]],
) -> list[str]:
    """Derive impact warnings from the newest report that affected the unit.

    Without an explicit dependency graph, modules affected by the same commit
    are used as the data-driven proxy for upstream/downstream impact.
    """
    if not reports:
        return []
    latest = reports[0]
    unit_names = {unit.get("id"): unit.get("name") or unit.get("id") for unit in registry.get("units", [])}
    others = [other for other in (latest.get("affected_units") or []) if other != unit_id]
    if not others:
        return []
    names = "、".join(f"{other}（{unit_names.get(other, other)}）" for other in others)
    commit_label = latest.get("commit_short") or str(latest.get("commit") or "")[:8]
    commit_part = f"最近一次变更（{commit_label}）" if commit_label else "最近一次变更"
    return [f"{commit_part}同时影响：{names}"]


def estimate_tokens(text: str) -> int:
    """Rough token estimate: one token per CJK char, four ASCII chars per token."""
    if not text:
        return 0
    cjk = sum(1 for char in text if _CJK_RE.match(char))
    other = len(text) - cjk
    return cjk + max(1, (other + 3) // 4)


def truncate_text(text: str, max_tokens: int, suffix: str = "…") -> tuple[str, bool]:
    """Truncate text to a token budget, cutting at a punctuation boundary when possible.

    Every character is conservatively counted as one token, so the returned text
    is guaranteed never to exceed the budget even though ``estimate_tokens``
    reports smaller numbers for ASCII text.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer.")
    if estimate_tokens(text) <= max_tokens:
        return text, False
    if max_tokens <= estimate_tokens(suffix):
        return suffix[:max_tokens], True
    budget = max_tokens - estimate_tokens(suffix)
    kept: list[str] = []
    used = 0
    for char in text:
        if used + 1 > budget:
            break
        kept.append(char)
        used += 1
    result = "".join(kept).rstrip("，。；、,;. \t")
    return result + suffix, True


def build_context(
    file_path: str,
    project_root: str | Path,
    registry_path: str | Path | None = None,
    reports_path: str | Path | None = None,
    patches_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the structured injection context for a code file."""
    repo_path = resolve_input_path(file_path, project_root)
    if registry_path is None:
        registry_path = Path(project_root) / ".knowledge-ci" / "data" / "registry.json"

    unit = match_unit_record(repo_path, registry_path)
    if unit is None:
        return {"matched": False, "file_path": repo_path}

    # Schema v2 state machine: only active knowledge (or legacy units without
    # a status) is injected; proposed/under_review/outdated/retired are not.
    unit_id = unit.get("id", "")
    if not is_injectable(unit):
        return {
            "matched": False,
            "file_path": repo_path,
            "unit_id": unit_id,
            "inactive_status": unit.get("status"),
        }

    registry = load_json(registry_path)
    patches = applied_patches(patches_path, unit_id) if patches_path else []
    reports = related_reports(reports_path, unit_id) if reports_path else []

    return {
        "matched": True,
        "file_path": repo_path,
        "unit_id": unit_id,
        "unit_name": unit.get("title") or unit.get("name") or unit_id,
        "status": unit.get("status") or "active",
        "risk_level": unit.get("risk_level", "LOW"),
        "knowledge_summary": delta_to_text(unit.get("knowledge_delta")),
        "history_decisions": history_decisions(unit, patches),
        "impact_warnings": impact_warnings(unit_id, registry, reports),
        "related_docs": unit.get("related_docs") or [],
        "last_verified": unit.get("last_verified") or "",
        "version": unit.get("version"),
        "code_hash": unit.get("code_hash") or "",
    }


def build_block_lines(context: dict[str, Any]) -> list[str]:
    """Format the core injection lines, untrimmed."""
    unit_id = context.get("unit_id", "unknown")
    name = context.get("unit_name") or unit_id
    risk = context.get("risk_level", "LOW")
    lines = [f"模块：{name} ({unit_id}) — 风险等级：{risk}"]
    lines.append(f"知识摘要：{context.get('knowledge_summary') or '（暂无知识内容，待补充）'}")
    history = context.get("history_decisions") or []
    lines.append("历史决策：" + ("；".join(history) if history else "暂无已落地的历史决策记录"))
    warnings = context.get("impact_warnings") or []
    lines.append("影响警告：" + ("；".join(warnings) if warnings else "暂无已识别的上下游影响记录"))
    lines.append(f"最近验证：{context.get('last_verified') or '未知'}")
    return lines


def trim_lines(lines: list[str], max_tokens: int) -> list[str]:
    """Compress the knowledge block to the token budget.

    The knowledge summary is compressed first (background compression), then
    historical decisions, then impact warnings. Business rules and the risk
    level line are always kept as long as the budget allows.
    """
    if estimate_tokens("\n".join(lines)) <= max_tokens:
        return lines

    for index, prefix in ((1, "知识摘要："), (2, "历史决策："), (3, "影响警告：")):
        others = estimate_tokens("\n".join(line for i, line in enumerate(lines) if i != index))
        available = max_tokens - others - estimate_tokens(prefix)
        if available <= 0:
            continue
        content = lines[index][len(prefix):] if lines[index].startswith(prefix) else lines[index]
        trimmed, _ = truncate_text(content, available)
        lines[index] = prefix + trimmed
        if estimate_tokens("\n".join(lines)) <= max_tokens:
            return lines

    if estimate_tokens("\n".join(lines)) > max_tokens:
        lines = [truncate_text("\n".join(lines), max_tokens)[0]]
    return lines


def knowledge_block(context: dict[str, Any], max_tokens: int = DEFAULT_MAX_TOKENS) -> tuple[str, int]:
    """Return the token-budgeted knowledge block and its estimated token count."""
    budget = max(1, max_tokens)
    lines = trim_lines(build_block_lines(context), budget)
    block = "\n".join(lines)
    return block, estimate_tokens(block)


def feedback_footer(unit_id: str, file_path: str, base_url: str = "http://localhost:8080") -> str:
    base = base_url.rstrip("/")
    common = {"unit_id": unit_id}
    if file_path:
        common["file"] = file_path
    useful_url = f"{base}/feedback?" + urlencode({**common, "feedback": "useful"})
    improve_url = f"{base}/feedback?" + urlencode({**common, "feedback": "improve"})
    return (
        "---\n"
        "这条知识对你有帮助吗？[👍 有帮助] [👎 需改进]\n"
        f"👍 有帮助：{useful_url}\n"
        f"👎 需改进：{improve_url}"
    )


def format_context(
    context: dict[str, Any],
    base_url: str = "http://localhost:8080",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    include_feedback: bool = True,
    verbose: bool = False,
) -> str:
    """Format the injection context for stdout."""
    if not context.get("matched"):
        return format_unmatched(context.get("file_path", ""), context.get("inactive_status"))

    block, tokens = knowledge_block(context, max_tokens)
    output = "【Knowledge CI 上下文】\n" + block
    if verbose:
        related = context.get("related_docs") or []
        if related:
            output += "\n关联文档：" + "、".join(related)
        output += f"\n（注入内容约 {tokens} tokens / 上限 {max_tokens}）"
    if include_feedback:
        output += "\n" + feedback_footer(context.get("unit_id", ""), context.get("file_path", ""), base_url)
    return output


def format_unmatched(file_path: str = "", inactive_status: str | None = None) -> str:
    if inactive_status:
        return "\n".join(
            [
                "【Knowledge CI 上下文】",
                f"该文件匹配到的知识单元当前状态为 {inactive_status}，暂不注入。",
                f"（文件：{file_path}）" if file_path else "（文件：未知）",
                "知识状态机：proposed → under_review → active → outdated → retired；仅 active 状态注入。",
            ]
        )
    lines = ["【Knowledge CI 上下文】", "该文件暂无知识记录，建议补充。"]
    if file_path:
        lines.append(f"（文件：{file_path}）")
    lines.append("建议：将核心业务规则、风险等级和关联文档录入 data/registry.json 后重新运行。")
    return "\n".join(lines)


def record_feedback(
    feedback_path: str | Path,
    unit_id: str,
    file_path: str,
    feedback: str,
    source: str = "web",
    adopted: bool | None = None,
) -> dict[str, Any]:
    """Append one feedback record to a JSON Lines log.

    ``adopted`` marks whether the injected knowledge was actually used
    (True/False) and feeds the hit-rate metric; None leaves it unreported.
    """
    if feedback not in {"useful", "improve"}:
        raise ValueError("feedback must be 'useful' or 'improve'.")
    record = {
        "unit_id": unit_id or "",
        "file": file_path or "",
        "feedback": feedback,
        "source": source,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if adopted is not None:
        record["adopted"] = bool(adopted)
    path = Path(feedback_path)
    with _FEEDBACK_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
