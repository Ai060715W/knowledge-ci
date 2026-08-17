from __future__ import annotations

"""Migrate a knowledge registry from schema v1 to schema v2.

The migration is idempotent and safe:

- ``--dry-run`` prints what would change without writing anything.
- A backup ``<registry>.v1.bak`` is written before any real change.
- ``--rollback`` restores the most recent backup.
- Registries already at v2 are left untouched unless ``--force`` is given.
"""

import argparse
import json
import shutil
from pathlib import Path

from src.config import load_project_paths, resolve_config_path
from src.registry.schema import migrate_registry, validate_registry
from src.registry.store import atomic_write_json


HELP = "Migrate a registry from schema v1 to v2."

BACKUP_SUFFIX = ".v1.bak"


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate a knowledge registry from schema v1 to v2.", add_help=add_help
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="Path to registry.json (overrides the config-derived path when given).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to .knowledge-ci/config.yaml (auto-discovered from the cwd by default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the migration result without writing anything.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing the .v1.bak backup file.",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Restore the registry from its .v1.bak backup.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-normalize a registry that is already at schema v2.",
    )
    return parser


def resolve_registry_path(args: argparse.Namespace) -> Path:
    if args.registry:
        return Path(args.registry).resolve()
    paths = load_project_paths(resolve_config_path(args.config))
    return Path(paths["registry_path"])


def load_registry_document(registry_path: Path) -> dict:
    with registry_path.open("r", encoding="utf-8") as registry_file:
        return json.load(registry_file)


def run(args: argparse.Namespace) -> int:
    registry_path = resolve_registry_path(args)
    backup_path = registry_path.with_name(registry_path.name + BACKUP_SUFFIX)

    if args.rollback:
        if not backup_path.is_file():
            print(f"No backup found at {backup_path}; nothing to roll back.")
            return 1
        if args.dry_run:
            print(f"[dry-run] Would restore {registry_path} from {backup_path}")
            return 0
        shutil.copy2(backup_path, registry_path)
        print(f"Restored {registry_path} from {backup_path}.")
        return 0

    if not registry_path.is_file():
        print(f"Registry not found: {registry_path}")
        return 1

    registry = load_registry_document(registry_path)
    current_version = int(registry.get("version", 1))
    if current_version >= 2 and not args.force:
        print(f"{registry_path} is already at schema v2 (version={current_version}); nothing to do.")
        return 0

    migrated, warnings = migrate_registry(registry)
    for warning in warnings:
        print(f"Warning: {warning}")

    errors = validate_registry(migrated)
    if errors:
        print("Migration produced an invalid registry; nothing was written:")
        for error in errors:
            print(f"  - {error}")
        return 1

    unit_count = len(migrated.get("units", []))
    if args.dry_run:
        print(f"[dry-run] Would migrate {registry_path} (version {current_version} -> 2, {unit_count} unit(s)).")
        print("[dry-run] No files were written.")
        return 0

    if not args.no_backup:
        shutil.copy2(registry_path, backup_path)
        print(f"Backup written: {backup_path}")

    atomic_write_json(registry_path, migrated)
    print(f"Migrated {registry_path} (version {current_version} -> 2, {unit_count} unit(s)).")
    print(f"Rollback anytime with: kc migrate --rollback --registry {registry_path}")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
