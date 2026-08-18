from __future__ import annotations

"""Unified ``kc`` command line interface.

Dispatches to the command modules in this package; each legacy
``scripts/<name>.py`` entry point remains available and behaves identically.
"""

import argparse
import sys
from typing import Any

from src.cli import (
    apply,
    analyze,
    ask_owner,
    check_llm,
    discover,
    feedback,
    freshness,
    generate,
    init,
    inject,
    metrics,
    migrate,
    webhook,
)


COMMANDS: dict[str, Any] = {
    "init": init,
    "analyze": analyze,
    "discover": discover,
    "ask-owner": ask_owner,
    "freshness": freshness,
    "generate": generate,
    "apply": apply,
    "inject": inject,
    "feedback": feedback,
    "check-llm": check_llm,
    "migrate": migrate,
    "webhook": webhook,
    "metrics": metrics,
}


def _reconfigure_streams() -> None:
    # Windows consoles default to GBK, which cannot encode the emoji in some
    # outputs; replace instead of crashing on unencodable characters.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kc",
        description="Knowledge CI: continuous discovery, verification, injection and evolution of software knowledge.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, module in COMMANDS.items():
        subparser = subparsers.add_parser(
            name,
            help=module.HELP,
            parents=[module.build_parser(add_help=False)],
        )
        subparser.set_defaults(_run=module.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    args = build_parser().parse_args(argv)
    return args._run(args)


if __name__ == "__main__":
    raise SystemExit(main())
