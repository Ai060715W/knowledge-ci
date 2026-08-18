from __future__ import annotations

"""kc run: one command through the full agent pipeline."""

import argparse
from pathlib import Path

from src.agents.orchestrator import PIPELINE_ORDER, run_pipeline
from src.config import CONFIG_DEFAULTS, load_project_paths, load_settings, resolve_config_path


HELP = "Run the full agent pipeline end to end."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the A2A pipeline: analysis -> evidence -> knowledge -> risk -> patch -> review -> injection.",
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
        "--patches",
        default=None,
        help="Directory for PENDING patch proposals (default: patches_path from --config).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for the run report (default: reports_path, else cwd).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Number of top modules for the analysis stage (default: discovery.top_k from config).",
    )
    parser.add_argument(
        "--stop-after",
        default=None,
        choices=list(PIPELINE_ORDER),
        help="Run the pipeline only up to (and including) this stage.",
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
        else (paths["registry_path"] if paths else None)
    )
    patches_path = Path(args.patches) if args.patches else (paths["patches_path"] if paths else None)
    reports_path = paths["reports_path"] if paths else None
    out_dir = Path(args.out) if args.out else (reports_path if reports_path else Path.cwd())

    report, output_path = run_pipeline(
        repo_root=repo_root,
        settings=settings,
        top_k=args.top_k,
        out_dir=out_dir,
        registry_path=registry_path,
        reports_path=reports_path,
        patches_path=patches_path,
        stop_after=args.stop_after,
    )

    print(f"Wrote run report: {output_path}")
    print("Pipeline:")
    for stage in report["pipeline"]:
        status = stage["status"]
        detail = stage.get("summary") or stage.get("reason") or stage.get("error") or ""
        print(f"  [{status:8s}] {stage['name']:12s} {detail}")
    drafts = report.get("drafts", [])
    proposals = [item for group in report.get("proposals", []) for item in group.get("proposals", [])]
    print(f"Drafts: {len(drafts)}")
    print(f"Patch proposals (PENDING): {len(proposals)}")
    previews = report.get("injection_previews", [])
    matched_previews = sum(1 for preview in previews if preview.get("matched"))
    print(f"Injection previews: {len(previews)} ({matched_previews} matched)")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
