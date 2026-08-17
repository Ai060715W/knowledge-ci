from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_project_paths, resolve_config_path
from src.patch.pr_manager import apply_patch_to_registry, load_json, mark_patch_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply an approved Knowledge CI patch to registry.json.")
    parser.add_argument("--patch", required=True, help="Path to patch_<id>.json.")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = load_project_paths(resolve_config_path(args.config))
    patch = load_json(args.patch)
    apply_patch_to_registry(patch, paths["registry_path"])
    mark_patch_status(args.patch, "APPLIED")
    print(f"Applied patch {patch['patch_id']} to {paths['registry_path']} (status -> APPLIED)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
