from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_project_paths, resolve_config_path
from src.impact.analyzer import analyze_commit, write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a commit and write a Knowledge CI impact report.")
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


def main() -> int:
    args = build_parser().parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
