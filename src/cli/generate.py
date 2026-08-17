from __future__ import annotations

"""Generate a Knowledge CI patch for an impacted unit."""

import argparse
from pathlib import Path

from src.config import load_project_paths, resolve_config_path
from src.patch.generator import build_patch


HELP = "Generate a knowledge patch for an impacted unit."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Knowledge CI patch for an impacted unit.", add_help=add_help
    )
    parser.add_argument("--commit", required=True, help="Commit hash or short hash with an existing impact report.")
    parser.add_argument("--unit", required=True, help="Knowledge unit id to patch.")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    parser.add_argument(
        "--mock-response",
        help="Raw JSON Delta ops for local validation when OPENAI_API_KEY is unavailable.",
    )
    parser.add_argument(
        "--mock-response-file",
        help="Path to a file containing raw JSON Delta ops for local validation.",
    )
    parser.add_argument(
        "--review-feedback",
        help="Reviewer feedback from a rejected patch; appended to the prompt for correction.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    paths = load_project_paths(resolve_config_path(args.config))

    mock_response = args.mock_response
    if args.mock_response_file:
        mock_response = Path(args.mock_response_file).read_text(encoding="utf-8")

    patch, output_path = build_patch(
        commit=args.commit,
        unit_id=args.unit,
        registry_path=paths["registry_path"],
        reports_path=paths["reports_path"],
        patches_path=paths["patches_path"],
        model=paths["model"],
        mock_response=mock_response,
        review_feedback=args.review_feedback,
    )
    print(f"Wrote patch: {output_path}")
    print(f"Patch ID: {patch['patch_id']}")
    print(f"Status: {patch['status']}")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
