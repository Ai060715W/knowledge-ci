from __future__ import annotations

"""kc notify: per-owner knowledge-change notifications into notify_<ts>.json."""

import argparse
from pathlib import Path

from src.config import load_project_paths, resolve_config_path
from src.metrics.notify import build_notification_report, write_notification_report


HELP = "Group pending/changed knowledge by owner into a local notification report."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan patches (PENDING/APPLIED/REJECTED) and registry unit states, then write a "
            "per-owner notification summary to reports/notify_<ts>.json. Local file only; "
            "no network, no LLM."
        ),
        add_help=add_help,
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
        help="Directory with patch_*.json files (default: patches_path).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for notify_<ts>.json (default: reports_path).",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    paths = None
    if args.config or not any([args.registry, args.patches, args.out]):
        config_path = resolve_config_path(args.config)
        paths = load_project_paths(config_path)

    registry_path = (
        Path(args.registry)
        if args.registry
        else (paths["registry_path"] if paths else None)
    )
    patches_path = Path(args.patches) if args.patches else (paths["patches_path"] if paths else None)
    out_dir = Path(args.out) if args.out else (paths["reports_path"] if paths else None)

    if registry_path is None:
        raise SystemExit(
            "未找到 registry：请在项目目录运行（自动发现 .knowledge-ci/config.yaml），"
            "或用 --registry 显式指定。\n"
            "No registry found. Run inside a configured project or pass --registry."
        )
    if out_dir is None:
        raise SystemExit(
            "未确定输出目录：请用 --out 指定，或在项目目录运行以使用配置的 reports_path。\n"
            "No output directory. Pass --out or run inside a configured project."
        )

    report = build_notification_report(registry_path, patches_path)
    output_path = write_notification_report(report, out_dir)

    summary = report["summary"]
    print(f"Wrote notifications: {output_path}")
    print(f"  owners: {summary['owners']}")
    print(f"  pending review: {summary['pending_review']} patch(es)")
    print(f"  changed: {summary['changed']} patch(es)")
    print(f"  rejected: {summary['rejected']} patch(es)")
    print(f"  units under review: {summary['units_under_review']}, outdated: {summary['units_outdated']}")
    if summary["warnings"]:
        print(f"  warnings: {summary['warnings']} (see report)")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
