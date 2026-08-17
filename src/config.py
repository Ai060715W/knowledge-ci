from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


CONFIG_DIR_NAME = ".knowledge-ci"


def load_config(config_path: str | Path) -> dict[str, Any]:
    """Load a config.yaml file."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def discover_config_path(cwd: str | Path | None = None) -> Path | None:
    """Find <project>/.knowledge-ci/config.yaml under the given directory (default: cwd)."""
    base = Path(cwd) if cwd else Path.cwd()
    candidate = base / CONFIG_DIR_NAME / "config.yaml"
    return candidate if candidate.is_file() else None


def resolve_project_root(config_path: str | Path) -> Path:
    """Resolve the target project root from a config file.

    All relative paths in the config are anchored to the config file's directory,
    so a typical <project>/.knowledge-ci/config.yaml uses ``project_path: ".."``.
    """
    config_file = Path(config_path).resolve()
    config = load_config(config_file)
    return (config_file.parent / config.get("project_path", ".")).resolve()


def resolve_config_path(config_arg: str | None) -> Path:
    """Resolve the config path from an explicit --config value or cwd discovery."""
    if config_arg:
        path = Path(config_arg)
        if not path.is_file():
            raise SystemExit(f"Config file not found: {path}")
        return path
    discovered = discover_config_path()
    if discovered is not None:
        return discovered
    raise SystemExit(
        "未找到 .knowledge-ci/config.yaml。"
        "请先在项目目录运行 init_project.py 初始化，或使用 --config 指定配置文件。\n"
        "No .knowledge-ci/config.yaml found. Run init_project.py in the project first, "
        "or pass --config explicitly."
    )


def load_project_paths(config_path: str | Path) -> dict[str, Any]:
    """Resolve every path the CLI scripts need from one config file.

    Returns the resolved project root, registry/reports/patches/feedback paths,
    and the configured model name.
    """
    config_file = Path(config_path).resolve()
    config = load_config(config_file)
    config_dir = config_file.parent
    return {
        "config_path": config_file,
        "config_dir": config_dir,
        "project_root": (config_dir / config.get("project_path", ".")).resolve(),
        "registry_path": (config_dir / config.get("registry_path", "data/registry.json")).resolve(),
        "reports_path": (config_dir / config.get("reports_path", "data/reports")).resolve(),
        "patches_path": (config_dir / config.get("patches_path", "data/patches")).resolve(),
        "feedback_path": (config_dir / config.get("feedback_path", "data/feedback.jsonl")).resolve(),
        "model": config.get("model", "deepseek-chat"),
    }
