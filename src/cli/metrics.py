from __future__ import annotations

"""kc metrics: compute the four design-document KPIs into metrics.json."""

import argparse
from pathlib import Path

from src.config import CONFIG_DEFAULTS, load_project_paths, load_settings, resolve_config_path
from src.metrics.metrics import compute_metrics


HELP = "Compute the four observability KPIs."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute coverage, freshness rate, hit rate, and confirmation rate.",
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
        "--reports",
        default=None,
        help="Directory with discovery_*.json reports (default: reports_path).",
    )
    parser.add_argument(
        "--patches",
        default=None,
        help="Directory with patch_*.json files (default: patches_path).",
    )
    parser.add_argument(
        "--feedback",
        default=None,
        help="Path to the feedback JSONL log (default: feedback_path).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for metrics.json (default: metrics_path).",
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
    reports_path = Path(args.reports) if args.reports else (paths["reports_path"] if paths else None)
    patches_path = Path(args.patches) if args.patches else (paths["patches_path"] if paths else None)
    feedback_path = Path(args.feedback) if args.feedback else (paths["feedback_path"] if paths else None)
    out_dir = Path(args.out) if args.out else (paths["metrics_path"] if paths else None)

    report, output_path = compute_metrics(
        repo_root=repo_root,
        registry_path=registry_path,
        reports_path=reports_path,
        patches_path=patches_path,
        feedback_path=feedback_path,
        out_dir=out_dir,
        settings=settings,
    )

    print(f"Wrote metrics: {output_path}")
    for metric in report["metrics"]:
        value = f"{metric['value']:.4f}" if metric["value"] is not None else "n/a"
        print(f"  {metric['key']:20s} {value:8s}  ({metric['numerator']}/{metric['denominator']})")
    print(f"Status distribution: {report['status_distribution'].get('counts')}")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
