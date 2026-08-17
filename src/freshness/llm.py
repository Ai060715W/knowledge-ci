from __future__ import annotations

"""Layer 4 of the freshness pipeline: the LLM final verdict.

The model receives the unit's knowledge text, its evidence chain, the commits
that touched it, the normalized semantic diff summary, and the layer-1..3
decision log — then must answer in a strict JSON schema with exactly one of
four verdicts. Validation failures are fed back for retry (up to 3 attempts),
and ``--mock-response-file`` supports fully offline runs.
"""

import json
import os
from typing import Any

import jsonschema
from openai import OpenAI

from src.patch.delta import validate_delta_ops

__all__ = [
    "VERDICTS",
    "VerificationError",
    "build_freshness_prompt",
    "judge_freshness",
    "parse_verdict",
]

#: The four freshness verdicts of the design document.
VERDICTS: tuple[str, ...] = ("still_valid", "partial_update", "outdated", "new_knowledge")

#: Strict output contract enforced via jsonschema before anything is trusted.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict", "reasoning"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reasoning": {"type": "string", "minLength": 1},
        "patch_ops": {"type": "array"},
        "new_knowledge": {
            "type": "object",
            "required": ["title", "summary", "rationale"],
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "rationale": {"type": "string"},
                "symbols": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}


class VerificationError(ValueError):
    """Raised when the model output fails the verdict contract."""


def _fuzzy_words_ok(text: str) -> bool:
    return not any(word in text for word in ("可能", "大概"))


def parse_verdict(response_text: str) -> dict[str, Any]:
    """Parse and validate a model verdict (raw JSON only, no code fences)."""
    text = response_text.strip()
    if text.startswith("```"):
        raise VerificationError("Model response must be raw JSON, not a Markdown code block.")
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError as error:
        raise VerificationError(f"Invalid JSON: {error}") from error

    errors = [error.message for error in jsonschema.Draft7Validator(VERDICT_SCHEMA).iter_errors(verdict)]
    if errors:
        raise VerificationError("Schema violation: " + "; ".join(errors))

    if not _fuzzy_words_ok(verdict.get("reasoning", "")):
        raise VerificationError("Reasoning contains forbidden fuzzy wording (可能/大概).")

    if verdict["verdict"] == "partial_update":
        try:
            validate_delta_ops(verdict.get("patch_ops"))
        except ValueError as error:
            raise VerificationError(f"patch_ops invalid: {error}") from error

    return verdict


def build_freshness_prompt(
    unit: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """Assemble the layer-4 prompt from the unit and the layer-1..3 log."""
    evidence = ", ".join(
        f"{item.get('type')}:{item.get('id', item.get('short_id', ''))[:12]}"
        for item in (unit.get("evidence") or [])[:6]
    ) or "（无）"
    commits = "\n".join(
        f"- {item.get('id', '')[:10]} {item.get('subject', '')[:80]}"
        for item in context.get("commits", [])[:8]
    ) or "（无）"
    ast_summary = "\n".join(
        f"- {entry.get('path')}: {entry.get('reason')}"
        for entry in context.get("ast_summary", [])
    ) or "（无）"
    impact = context.get("impact_reason", "")
    diff_excerpts = "\n".join(context.get("diff_excerpts", [])[:40]) or "（无）"

    return f"""你是资深架构师，负责判断一条团队知识是否仍然与代码一致。

知识单元：
- 标题：{unit.get('title', '')}
- 结论：{unit.get('summary', '')}
- 依据：{unit.get('rationale', '')}
- 知识正文：{unit.get('knowledge_delta', {}).get('ops', [])}
- 证据链：{evidence}
- 适用范围：files={unit.get('scope', {}).get('files', [])} symbols={unit.get('scope', {}).get('symbols', [])}

变更事实（前三层过滤后的结果）：
- 触及提交：\n{commits}
- AST 语义过滤：\n{ast_summary}
- 依赖影响判定：{impact}
- 语义 diff 节选：\n{diff_excerpts}

请仅输出 JSON 对象（不要 Markdown、不要解释），字段：
{{"verdict": "still_valid|partial_update|outdated|new_knowledge", "reasoning": "判定理由（中文，简洁）"}}

- still_valid：知识仍然成立。
- partial_update：知识部分失效，必须同时输出 "patch_ops"（Quill Delta 操作数组，对知识正文的修改）。
- outdated：知识整体失效，应退役或重写。
- new_knowledge：变更产生了新的设计知识，必须同时输出 "new_knowledge"（{{"title","summary","rationale","symbols"}}）。

要求：
- 禁止使用“可能”“大概”等模糊词。
- 只依据输入判断，不编造输入中不存在的依赖或事实。
- patch_ops 的 retain/delete 数值必须与知识正文逐字一致；拿不准时用全量替换（delete=正文总字符数）。"""


def _call_model(prompt: str, model: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set. Use --mock-response-file for offline runs.")
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def judge_freshness(
    unit: dict[str, Any],
    context: dict[str, Any],
    model: str,
    mock_response: str | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Run layer 4 with schema validation and self-correction retries.

    Returns {"verdict", "reasoning", "patch_ops"?, "new_knowledge"?,
    "attempts", "prompt"}. Raises RuntimeError when all attempts fail.
    """
    prompt = build_freshness_prompt(unit, context)
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        attempt_prompt = prompt
        if last_error:
            attempt_prompt = (
                prompt
                + f"\n\n上一次输出未通过校验：{last_error}\n请修正后仅输出合法 JSON 对象。"
            )
        response_text = (
            mock_response if mock_response is not None else _call_model(attempt_prompt, model)
        )
        try:
            verdict = parse_verdict(response_text)
            verdict["attempts"] = attempt
            verdict["prompt"] = prompt
            return verdict
        except VerificationError as error:
            last_error = str(error)
            continue
    raise RuntimeError(f"Freshness verdict failed after {max_attempts} attempt(s): {last_error}")
