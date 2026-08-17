from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.patch.delta import DeltaValidationError, apply_delta_ops, delta_to_text, text_to_delta, validate_delta_ops
from src.patch.prompts import build_prompt


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
FUZZY_WORDS = ("可能", "大概")


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def find_unit(registry: dict[str, Any], unit_id: str) -> dict[str, Any]:
    for unit in registry.get("units", []):
        if unit.get("id") == unit_id:
            return unit
    raise KeyError(f"Unknown knowledge unit: {unit_id}")


def find_report_path(reports_path: str | Path, commit: str) -> Path:
    reports_dir = Path(reports_path)
    candidates = sorted(reports_dir.glob(f"impact_{commit}*.json"))
    if not candidates:
        raise FileNotFoundError(f"No impact report found for commit {commit} in {reports_dir}.")
    return candidates[0]


def changes_for_unit(report: dict[str, Any], unit_id: str) -> list[dict[str, Any]]:
    return [item for item in report.get("changed_files", []) if item.get("unit_id") == unit_id]


def summarize_unit_changes(changes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for change in changes:
        summary = change.get("summary", {})
        lines.append(f"- {change.get('path')} ({change.get('status')})")
        for key in ("functions", "classes", "constants"):
            values = summary.get(key) or []
            if values:
                lines.append(f"  {key}: {', '.join(values)}")
        diff_excerpt = summary.get("diff_excerpt") or []
        if diff_excerpt:
            lines.append("  diff:")
            lines.extend(f"    {line}" for line in diff_excerpt[:20])
    return "\n".join(lines)


def parse_model_delta(response_text: str) -> list[dict[str, Any]]:
    text = response_text.strip()
    if text.startswith("```"):
        raise ValueError("Model response must be raw JSON, not a Markdown code block.")
    ops = json.loads(text)
    validate_delta_ops(ops)
    return ops


def call_openai_for_delta(prompt: str, model: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set. Use --mock-response for local validation.")
    # Uses the OpenAI SDK against whatever OPENAI_BASE_URL points to, so both
    # OpenAI and DeepSeek's compatible endpoint work through the same path.
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def next_patch_id(patches_path: str | Path, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    date_part = current.strftime("%Y%m%d")
    existing = sorted(Path(patches_path).glob(f"patch_kp_{date_part}_*.json"))
    return f"kp_{date_part}_{len(existing) + 1:03d}"


def encode_preview_payload(old_delta: dict[str, Any], patch_ops: list[dict[str, Any]]) -> str:
    old_text = delta_to_text(old_delta)
    new_text = apply_delta_ops(old_text, patch_ops)
    payload = {
        "old_delta": old_delta,
        "patch_ops": patch_ops,
        "new_delta": text_to_delta(new_text),
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def build_patch(
    commit: str,
    unit_id: str,
    registry_path: str | Path,
    reports_path: str | Path,
    patches_path: str | Path,
    model: str,
    mock_response: str | None = None,
    max_attempts: int = 3,
    review_feedback: str | None = None,
) -> tuple[dict[str, Any], Path]:
    registry = load_json(registry_path)
    unit = find_unit(registry, unit_id)
    report_path = find_report_path(reports_path, commit)
    report = load_json(report_path)
    changes = changes_for_unit(report, unit_id)
    if not changes:
        raise ValueError(f"Impact report {report_path} has no changes for unit {unit_id}.")

    old_delta = unit.get("knowledge_delta") or {"ops": []}
    old_text = delta_to_text(old_delta)
    code_diff = summarize_unit_changes(changes)
    changed_files = [change["path"] for change in changes]
    prompt = build_prompt(unit, code_diff, changed_files)
    if review_feedback:
        prompt += f"\n\n审核意见（必须据此修正后重新输出）：\n{review_feedback}"
    delta_ops: list[dict[str, Any]] | None = None
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        attempt_prompt = prompt
        if last_error:
            attempt_prompt = (
                prompt
                + f"\n\n上一次输出未通过校验，原因：{last_error}\n"
                "请修正后仅输出合法的 Quill Delta JSON 数组，不要输出任何解释。"
            )
        response_text = mock_response if mock_response is not None else call_openai_for_delta(attempt_prompt, model)
        try:
            delta_ops = parse_model_delta(response_text)
            new_text = apply_delta_ops(old_text, delta_ops)
            if any(word in new_text for word in FUZZY_WORDS):
                raise ValueError("Generated knowledge contains forbidden fuzzy wording.")
        except (DeltaValidationError, ValueError) as error:
            last_error = str(error)
            delta_ops = None
            continue
        break

    if delta_ops is None:
        raise RuntimeError(
            f"Patch generation failed after {max_attempts} attempt(s). Last error: {last_error}"
        )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    patch_id = next_patch_id(patches_path, now)
    reasoning = f"基于 commit {report.get('commit_short', commit)} 的代码变更摘要更新 {unit_id}。"
    preview_payload = encode_preview_payload(old_delta, delta_ops)
    patch = {
        "patch_id": patch_id,
        "status": "PENDING",
        "unit_id": unit_id,
        "commit": report.get("commit", commit),
        "old_version": int(unit.get("version", 0)),
        "new_version": int(unit.get("version", 0)) + 1,
        "risk_level": unit.get("risk_level", "LOW"),
        "delta_ops": delta_ops,
        "reasoning": reasoning,
        "affected_files": changed_files,
        "related_docs": unit.get("related_docs", []),
        "preview_delta": preview_payload,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "model": model,
        "source_report": str(report_path),
        "prompt": prompt,
    }

    output_dir = Path(patches_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"patch_{patch_id}.json"
    output_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return patch, output_path
