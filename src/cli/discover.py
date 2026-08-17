from __future__ import annotations

"""Discover hidden knowledge candidates in any repository.

Read-only analysis: builds the dependency graph, scores modules from git
history, detects structural signals, traces evidence, and writes candidate
drafts (status ``proposed``) plus owner questions into a JSON report.
"""

import argparse
from pathlib import Path

from src.config import CONFIG_DEFAULTS, load_project_paths, load_settings, resolve_config_path
from src.discovery.discover import run_discovery


HELP = "Discover hidden knowledge candidates in a repository."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover hidden knowledge candidates in any repository (read-only).",
        add_help=add_help,
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to the repository to analyze (any git or plain directory).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory for the discovery report (default: cwd, or reports_path when --config is given).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Number of top modules to analyze in depth (default: discovery.top_k from config, else 10).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (provides discovery settings and the default output dir).",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="Optional registry.json to annotate already-managed units in the report.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Rebuild the dependency graph instead of reusing the per-commit cache.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"Repository not found: {repo_root}")
        return 1

    if args.config:
        config_path = resolve_config_path(args.config)
        settings = load_settings(config_path)
        if args.out is None:
            out_dir = load_project_paths(config_path)["reports_path"]
        else:
            out_dir = Path(args.out)
    else:
        settings = CONFIG_DEFAULTS
        out_dir = Path(args.out) if args.out is not None else Path.cwd()

    report, output_path = run_discovery(
        repo_root=repo_root,
        settings=settings,
        top_k=args.top_k,
        out_dir=out_dir,
        registry_path=Path(args.registry) if args.registry else None,
        use_cache=not args.no_cache,
    )

    print(f"Wrote discovery report: {output_path}")
    print(f"Modules scanned: {report.get('modules_scanned', 0)}")
    if report.get("parse_errors"):
        print(f"Parse errors (skipped files): {len(report['parse_errors'])}")
    if not report.get("git", True):
        print("Note: not a git repository; scoring/evidence factors are empty.")
    print(f"Candidates: {report.get('candidate_count', 0)}")
    print(f"Questions: {report.get('question_count', 0)}")
    top_modules = report.get("top_modules", [])
    if top_modules:
        print("\nTop modules:")
        for entry in top_modules:
            unit_note = f" [unit: {entry['existing_unit']}]" if entry.get("existing_unit") else ""
            print(f"  {entry['score']:.3f}  {entry['module']}{unit_note}")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
