from __future__ import annotations

"""Event triggers: a stdlib webhook server for automatic pipeline runs.

Zero new dependencies (same ``http.server`` pattern as the feedback server).
v1 verifies GitHub events (``X-Hub-Signature-256``) and dispatches them to
local, configured checkouts. Everything the pipeline produces lands in the
target project's ``data/reports`` and ``data/patches`` directories — nothing
is ever applied automatically.
"""
