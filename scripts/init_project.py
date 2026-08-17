from __future__ import annotations

"""Compatibility wrapper around src.cli.init.

Behavior is identical to the original script; the implementation now lives in
``src/cli/init.py`` so it can also run through the unified ``kc`` CLI.
"""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli.init import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
