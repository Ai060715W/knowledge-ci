from __future__ import annotations

"""Analyze a commit and write a Knowledge CI impact report."""

import argparse
from pathlib import Path

from src.config import load_project_paths, resolve_config_path
from src.impact.analyzer import analyze_commit, write_report


HELP = "Analyze a commit and write an impact report."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze a commit and write a Knowledge CI impact report.", add_help=add_help
    )
    parser.add_argument("--hash", required=True, help="Commit hash to analyze in the configured project.")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    parser.add_argument(
        "--no-related-docs",
        action="store_true",
        help="Skip Markdown/RST symbol search for related documentation suggestions.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    paths = load_project_paths(resolve_config_path(args.config))

    report = analyze_commit(
        commit_hash=args.hash,
        project_path=paths["project_root"],
        registry_path=paths["registry_path"],
        include_related_docs=not args.no_related_docs,
    )
    output_path = write_report(report, paths["reports_path"])

    print(f"Wrote impact report: {output_path}")
    print(f"Changed code files: {len(report['changed_files'])}")
    print(f"Affected units: {', '.join(report['affected_units']) or 'none'}")
    print(f"Unmanaged files: {len(report['unmanaged_files'])}")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
