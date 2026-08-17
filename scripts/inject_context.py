from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_project_paths, resolve_config_path
from src.inject.context import (
    DEFAULT_MAX_TOKENS,
    build_context,
    format_context,
    knowledge_block,
)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--max-tokens must be a positive integer.")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inject Knowledge CI context for a code file before AI edits.")
    parser.add_argument(
        "--file",
        required=True,
        help="Path of the file about to be edited (repo-relative, project-prefixed, or absolute).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    parser.add_argument(
        "--max-tokens",
        type=positive_int,
        default=DEFAULT_MAX_TOKENS,
        help="Token budget for the injected knowledge block (default: 500).",
    )
    parser.add_argument(
        "--feedback-base-url",
        default="http://localhost:8080",
        help="Base URL used in the feedback links.",
    )
    parser.add_argument("--json", action="store_true", help="Print the context as JSON instead of the formatted block.")
    parser.add_argument("--verbose", action="store_true", help="Include related docs and token usage in the output.")
    return parser


def main() -> int:
    # Windows consoles default to GBK, which cannot encode the emoji in the
    # feedback footer; replace instead of crashing on unencodable characters.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    args = build_parser().parse_args()
    paths = load_project_paths(resolve_config_path(args.config))

    context = build_context(
        file_path=args.file,
        project_root=paths["project_root"],
        registry_path=paths["registry_path"],
        reports_path=paths["reports_path"],
        patches_path=paths["patches_path"],
    )

    if args.json:
        if context.get("matched"):
            _, tokens = knowledge_block(context, args.max_tokens)
            context["estimated_tokens"] = tokens
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0

    print(
        format_context(
            context,
            base_url=args.feedback_base_url,
            max_tokens=args.max_tokens,
            verbose=args.verbose,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
