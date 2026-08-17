from __future__ import annotations

"""Apply an approved Knowledge CI patch to registry.json."""

import argparse

from src.config import load_project_paths, resolve_config_path
from src.patch.pr_manager import apply_patch_to_registry, load_json, mark_patch_status


HELP = "Apply an approved patch to the registry."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply an approved Knowledge CI patch to registry.json.", add_help=add_help
    )
    parser.add_argument("--patch", required=True, help="Path to patch_<id>.json.")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    paths = load_project_paths(resolve_config_path(args.config))
    patch = load_json(args.patch)
    apply_patch_to_registry(patch, paths["registry_path"])
    mark_patch_status(args.patch, "APPLIED")
    print(f"Applied patch {patch['patch_id']} to {paths['registry_path']} (status -> APPLIED)")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
