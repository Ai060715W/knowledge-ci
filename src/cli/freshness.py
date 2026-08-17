from __future__ import annotations

"""Knowledge freshness: the four-layer staleness funnel.

``kc freshness`` is read-only by default; ``--apply`` only refreshes
verification timestamps and moves units through the schema v2 state machine.
Nothing here edits knowledge text — updates go through the existing patch
review pipeline.
"""

import argparse
from pathlib import Path

from src.config import CONFIG_DEFAULTS, load_project_paths, load_settings, resolve_config_path
from src.freshness.check import run_freshness


HELP = "Check knowledge freshness with the four-layer funnel."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check knowledge freshness (time -> AST -> dependency impact -> LLM).",
        add_help=add_help,
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Path to the repository (default: the configured project_root).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="Path to registry.json (overrides the --config derived path).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory for the freshness report (default: reports_path, else cwd).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check every non-retired unit instead of only active knowledge.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply safe bookkeeping: refresh last_verified/code_hash for fresh units and "
        "move outdated units through the state machine. Knowledge text is never edited.",
    )
    parser.add_argument(
        "--auto-patch",
        action="store_true",
        help="Write PENDING patch files for partial_update verdicts (still needs human review).",
    )
    parser.add_argument(
        "--patches",
        default=None,
        help="Directory for auto-generated PENDING patches (default: patches_path from --config).",
    )
    parser.add_argument(
        "--mock-response-file",
        default=None,
        help="File containing a raw verdict JSON for offline layer-4 runs.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Stop after the first three layers; in-scope units are reported as needs_llm.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.config:
        config_path = resolve_config_path(args.config)
        paths = load_project_paths(config_path)
        settings = load_settings(config_path)
    else:
        paths = None
        settings = CONFIG_DEFAULTS

    repo_root = Path(args.repo) if args.repo else (paths["project_root"] if paths else Path.cwd())
    registry_path = (
        Path(args.registry)
        if args.registry
        else (paths["registry_path"] if paths else repo_root / ".knowledge-ci" / "data" / "registry.json")
    )
    if args.out:
        out_dir = Path(args.out)
    elif paths:
        out_dir = paths["reports_path"]
    else:
        out_dir = Path.cwd()

    mock_response = None
    if args.mock_response_file:
        mock_response = Path(args.mock_response_file).read_text(encoding="utf-8")

    patches_path = Path(args.patches) if args.patches else (paths["patches_path"] if paths else None)

    report, output_path = run_freshness(
        repo_root=repo_root,
        registry_path=registry_path,
        settings=settings,
        out_dir=out_dir,
        include_all=args.all,
        apply=args.apply,
        auto_patch=args.auto_patch,
        mock_response=mock_response,
        no_llm=args.no_llm,
        patches_path=patches_path,
    )

    print(f"Wrote freshness report: {output_path}")
    summary = report.get("summary", {})
    print(
        " | ".join(
            f"{label}: {summary.get(label, 0)}"
            for label in ("still_valid", "partial_update", "outdated", "new_knowledge", "needs_llm", "error")
        )
    )
    for entry in report.get("units", []):
        note = f" [{entry.get('error')}]" if entry.get("error") else ""
        print(f"  {entry['verdict']:16s} {entry['unit_id']:40s} ({entry['basis']}){note}")
    if report.get("candidate_drafts"):
        print(f"New-knowledge drafts: {len(report['candidate_drafts'])} (see report)")
    if args.apply:
        print("Applied safe bookkeeping (timestamps/status transitions only).")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
