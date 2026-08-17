from __future__ import annotations

"""Check LLM connectivity for patch generation."""

import argparse
import os

from openai import OpenAI


HELP = "Check LLM connectivity."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the configured LLM endpoint answers.", add_help=add_help
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set.")
        return 1

    client = OpenAI()
    response = client.chat.completions.create(
        model=os.environ.get("KNOWLEDGE_CI_MODEL", "deepseek-chat"),
        messages=[{"role": "user", "content": "Hello from Knowledge CI POC. Reply with OK."}],
    )
    print(response.choices[0].message.content)
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
