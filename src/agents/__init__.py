from __future__ import annotations

"""A2A agents: analysis, evidence, knowledge, risk, review, injection.

Sequential in-process orchestration behind a stable, schema-first protocol
(see ``src/agents/base.py``); the orchestrator lives in ``orchestrator.py``.
"""

from src.agents.base import AGENTS, Agent  # noqa: F401
from src.agents import analysis, evidence, injection, knowledge, review, risk  # noqa: F401

__all__ = [
    "AGENTS",
    "Agent",
    "analysis",
    "evidence",
    "injection",
    "knowledge",
    "review",
    "risk",
]
