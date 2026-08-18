from __future__ import annotations

"""Compatibility wrapper around src.cli.metrics.

The implementation lives in ``src/cli/metrics.py`` so it can also run through
the unified ``kc`` CLI: ``kc metrics``.
"""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli.metrics import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
