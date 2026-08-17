from __future__ import annotations

import base64
from fnmatch import fnmatch
import json
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from src.patch.delta import apply_delta_ops, delta_to_text, text_to_delta
from src.registry.schema import transition_status


ROOT = Path(__file__).resolve().parents[2]


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def apply_patch_to_registry(
    patch: dict[str, Any],
    registry_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    registry_file = Path(registry_path)
    registry = load_json(registry_file)
    unit_id = patch["unit_id"]
    target = output_path or registry_file

    for unit in registry.get("units", []):
        if unit.get("id") != unit_id:
            continue

        old_text = delta_to_text(unit.get("knowledge_delta"))
        new_text = apply_delta_ops(old_text, patch["delta_ops"])
        unit["knowledge_delta"] = text_to_delta(new_text)
        unit["version"] = patch.get("new_version", int(unit.get("version", 0)) + 1)
        unit["last_verified"] = date.today().isoformat()
        unit["code_hash"] = str(patch.get("commit", ""))[:8]
        # An approved, landed patch makes the knowledge live again (schema v2
        # state machine); illegal transitions (e.g. from retired) raise.
        transition_status(unit, "active")
        registry["last_updated"] = patch.get("generated_at", registry.get("last_updated"))
        Path(target).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return registry

    raise KeyError(f"Unknown knowledge unit: {unit_id}")


def preview_url(patch: dict[str, Any], base_url: str = "http://localhost:8080/") -> str:
    delta = patch.get("preview_delta")
    if not delta:
        raw = json.dumps({"patch_ops": patch.get("delta_ops", [])}, ensure_ascii=False).encode("utf-8")
        delta = base64.urlsafe_b64encode(raw).decode("ascii")
    return f"{base_url}?delta={delta}"


def build_pr_body(patch: dict[str, Any], base_url: str = "http://localhost:8080/") -> str:
    affected_modules = [patch.get("unit_id", "unknown"), *patch.get("affected_files", [])]
    modules_text = "\n".join(f"- {item}" for item in affected_modules)
    reviewers = patch.get("reviewers") or []
    reviewer_text = "\n".join(f"- {reviewer}" for reviewer in reviewers) or "- TBD"
    return f"""## Knowledge Patch

- Patch ID: {patch.get("patch_id")}
- Unit: {patch.get("unit_id")}
- Old Version: {patch.get("old_version")}
- New Version: {patch.get("new_version")}
- Commit: {patch.get("commit")}

## Reasoning

{patch.get("reasoning", "")}

## Affected Modules

{modules_text}

## Reviewers

{reviewer_text}

## Preview

{preview_url(patch, base_url)}
"""


def discover_codeowners(repo_path: str | Path, paths: list[str]) -> list[str]:
    root = Path(repo_path)
    candidates = [
        root / "CODEOWNERS",
        root / ".github" / "CODEOWNERS",
        root / "docs" / "CODEOWNERS",
    ]
    owners: set[str] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        for raw_line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pattern, matched_owners = parts[0], parts[1:]
            normalized_pattern = pattern.lstrip("/")
            for path in paths:
                if fnmatch(path, normalized_pattern) or fnmatch(path, f"{normalized_pattern}*"):
                    owners.update(matched_owners)
    return sorted(owners)


def mark_patch_status(
    patch_path: str | Path,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    patch_file = Path(patch_path)
    patch = load_json(patch_file)
    patch["status"] = status
    if reason:
        patch["status_reason"] = reason
    patch_file.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return patch


def create_pr(
    patch: dict[str, Any],
    repo_path: str | Path,
    registry_path: str | Path = ROOT / "data" / "registry.json",
    dry_run: bool = True,
) -> dict[str, Any]:
    branch = f"knowledge-patch/{patch['patch_id']}"
    reviewers = discover_codeowners(repo_path, patch.get("affected_files", []))
    if reviewers:
        patch = {**patch, "reviewers": reviewers}
    body = build_pr_body(patch)
    if dry_run:
        return {"branch": branch, "title": f"Knowledge patch {patch['patch_id']}", "body": body, "dry_run": True}

    if not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("GITHUB_TOKEN is not set; cannot create GitHub PR.")

    subprocess.run(["git", "checkout", "-b", branch], cwd=repo_path, check=True)
    apply_patch_to_registry(patch, registry_path)
    subprocess.run(["git", "add", str(registry_path)], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", f"Apply knowledge patch {patch['patch_id']}"], cwd=repo_path, check=True)
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=repo_path, check=True)
    result = subprocess.run(
        ["gh", "pr", "create", "--title", f"Knowledge patch {patch['patch_id']}", "--body", body],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return {"branch": branch, "url": result.stdout.strip(), "dry_run": False}
