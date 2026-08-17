from __future__ import annotations

"""Compatibility wrapper around src.cli.feedback.

Behavior is identical to the original script; the implementation now lives in
``src/cli/feedback.py`` so it can also run through the unified ``kc`` CLI.
``create_server`` is re-exported for existing callers.
"""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli.feedback import FeedbackRequestHandler, create_server, main  # noqa: E402

__all__ = ["create_server", "FeedbackRequestHandler", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
