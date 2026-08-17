from __future__ import annotations

import os

from openai import OpenAI


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())

