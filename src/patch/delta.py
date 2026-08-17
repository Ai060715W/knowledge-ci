from __future__ import annotations

from typing import Any


class DeltaValidationError(ValueError):
    pass


def validate_delta_ops(ops: Any) -> list[dict[str, Any]]:
    if not isinstance(ops, list):
        raise DeltaValidationError("Delta ops must be a JSON array.")

    validated: list[dict[str, Any]] = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise DeltaValidationError(f"Delta op at index {index} must be an object.")

        action_keys = [key for key in ("retain", "delete", "insert") if key in op]
        if len(action_keys) != 1:
            raise DeltaValidationError(
                f"Delta op at index {index} must contain exactly one of retain, delete, or insert."
            )

        action = action_keys[0]
        value = op[action]
        if action in {"retain", "delete"}:
            if not isinstance(value, int) or value <= 0:
                raise DeltaValidationError(f"{action} at index {index} must be a positive integer.")
        elif not isinstance(value, str):
            raise DeltaValidationError(f"insert at index {index} must be a string.")

        validated.append(dict(op))

    return validated


def delta_to_text(delta: dict[str, Any] | None) -> str:
    if not delta:
        return ""

    ops = delta.get("ops")
    if not isinstance(ops, list):
        return ""

    text_parts: list[str] = []
    for op in ops:
        if isinstance(op, dict) and isinstance(op.get("insert"), str):
            text_parts.append(op["insert"])
    return "".join(text_parts)


def text_to_delta(text: str) -> dict[str, list[dict[str, str]]]:
    return {"ops": [{"insert": text}]}


def apply_delta_ops(old_text: str, ops: list[dict[str, Any]]) -> str:
    validate_delta_ops(ops)
    cursor = 0
    output: list[str] = []

    for op in ops:
        if "retain" in op:
            count = op["retain"]
            if cursor + count > len(old_text):
                raise DeltaValidationError("retain moves beyond the end of the old text.")
            output.append(old_text[cursor : cursor + count])
            cursor += count
        elif "delete" in op:
            count = op["delete"]
            if cursor + count > len(old_text):
                raise DeltaValidationError("delete moves beyond the end of the old text.")
            cursor += count
        elif "insert" in op:
            output.append(op["insert"])

    output.append(old_text[cursor:])
    return "".join(output)
