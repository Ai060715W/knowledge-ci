from __future__ import annotations

"""Compatibility wrapper around src.cli.migrate.

The implementation lives in ``src/cli/migrate.py`` so it can also run through
the unified ``kc`` CLI: ``kc migrate [--dry-run] [--rollback]``.
"""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli.migrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
