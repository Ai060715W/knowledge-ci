from __future__ import annotations

"""Command implementations for the unified ``kc`` CLI.

Each module keeps a ``build_parser``, a ``run(args)`` entry point (used by the
dispatcher in ``src.cli.main``), and a ``main()`` for standalone execution.
The legacy ``scripts/*.py`` files are thin wrappers around these modules.
"""
