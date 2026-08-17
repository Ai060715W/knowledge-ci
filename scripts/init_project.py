from __future__ import annotations

"""Initialize Knowledge CI for an existing project.

Creates a ``.knowledge-ci/`` directory inside the target project containing:

- ``config.yaml``         project path, data paths, and LLM model
- ``data/registry.json``  empty knowledge registry (schema-valid)
- ``data/registry.example.json``  a filled sample unit to copy from
- ``data/patches/`` and ``data/reports/`` output directories

Usage:
    python scripts/init_project.py --project /path/to/your/project
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CODE_SUFFIXES = {".py", ".js", ".ts", ".java"}
IGNORED_PARTS = {".git", ".hg", ".svn", "venv", ".venv", "node_modules", "__pycache__", ".knowledge-ci"}

CONFIG_TEMPLATE = """# Knowledge CI configuration.
# All relative paths are anchored to this file's directory (`.knowledge-ci/`).
# `project_path` points at your project root (`..` from here).
project_path: ".."

# Knowledge registry and generated artifacts.
registry_path: "data/registry.json"
reports_path: "data/reports"
patches_path: "data/patches"
feedback_path: "data/feedback.jsonl"

# LLM used for patch generation. Use `deepseek-chat`, `gpt-4o-mini`, or any
# model served by an OpenAI-compatible endpoint (OPENAI_BASE_URL env var).
model: "{model}"
"""

EMPTY_REGISTRY = {
    "version": 1,
    "last_updated": date.today().isoformat(),
    "units": [],
}

EXAMPLE_UNIT = {
    "id": "payment_retry",
    "name": "支付重试 / Payment retry",
    "file_pattern": "src/payment/retry.py",
    "risk_level": "HIGH",
    "knowledge_delta": {
        "ops": [
            {
                "insert": (
                    "支付重试上限为 3 次，超时后触发补偿流程。"
                    "The payment retry limit is 3; compensation runs after that."
                )
            }
        ]
    },
    "related_docs": ["docs/payment/spec.md"],
    "last_verified": date.today().isoformat(),
    "code_hash": "",
    "version": 1,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize Knowledge CI for a project.")
    parser.add_argument(
        "--project",
        default=".",
        help="Path to the target project root (default: current directory).",
    )
    parser.add_argument("--model", default="deepseek-chat", help="LLM model name for patch generation.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing config.yaml.")
    return parser


def suggest_units(project_root: Path, limit: int = 8) -> list[str]:
    candidates: list[tuple[int, str]] = []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in CODE_SUFFIXES:
            continue
        if any(part in IGNORED_PARTS for part in path.relative_to(project_root).parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        relative = path.relative_to(project_root).as_posix()
        if relative.startswith("tests/") or relative.startswith("test/"):
            continue
        candidates.append((size, relative))
    candidates.sort(reverse=True)
    return [name for _, name in candidates[:limit]]


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.project).resolve()
    if not project_root.is_dir():
        print(f"Project directory not found: {project_root}")
        return 1

    knowledge_dir = project_root / ".knowledge-ci"
    config_path = knowledge_dir / "config.yaml"
    data_dir = knowledge_dir / "data"
    registry_path = data_dir / "registry.json"
    example_path = data_dir / "registry.example.json"

    if config_path.exists() and not args.force:
        print(f"Already initialized: {config_path}")
        print("Use --force to overwrite, or edit the file directly.")
        return 1

    knowledge_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "patches").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)

    config_path.write_text(CONFIG_TEMPLATE.format(model=args.model), encoding="utf-8")
    if not registry_path.exists():
        registry_path.write_text(json.dumps(EMPTY_REGISTRY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    example_path.write_text(json.dumps(EXAMPLE_UNIT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Knowledge CI initialized for: {project_root}")
    print(f"  config:   {config_path}")
    print(f"  registry: {registry_path}")
    print(f"  example:  {example_path} (copy entries from here into registry.json)")

    suggestions = suggest_units(project_root)
    if suggestions:
        print("\nSuggested files for your first knowledge units:")
        for name in suggestions:
            print(f"  - {name}")

    print("""
Next steps:
  1. Add knowledge units to .knowledge-ci/data/registry.json
     (see registry.example.json for the schema; pick your core/high-risk modules).
  2. Set your LLM key:
     $env:OPENAI_API_KEY = "sk-..."
     Optional for DeepSeek: $env:OPENAI_BASE_URL = "https://api.deepseek.com"
     Verify with: python <knowledge-ci>/scripts/check_llm.py
  3. After a commit, analyze impact:
     python <knowledge-ci>/scripts/analyze_commit.py --hash <commit>
  4. Generate a patch, review it, then apply:
     python <knowledge-ci>/scripts/generate_patch.py --commit <commit> --unit <unit_id>
     python <knowledge-ci>/scripts/apply_patch.py --patch .knowledge-ci/data/patches/patch_<id>.json
  5. Inject context before AI edits:
     python <knowledge-ci>/scripts/inject_context.py --file <path/to/file>
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
