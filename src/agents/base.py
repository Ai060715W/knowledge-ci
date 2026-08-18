from __future__ import annotations

"""A2A-style agent protocol: interfaces first, implementations second.

Each agent declares its role, input schema, and output schema up front
(JSON Schema, draft-07). The orchestrator validates every message against
these schemas, so an agent can be reimplemented, swapped, or later moved to
a real A2A runtime without touching the pipeline.

v1 runs agents sequentially in-process; the protocol is the stable contract.
"""

from typing import Any, ClassVar

import jsonschema

__all__ = ["AGENTS", "Agent", "describe_agents", "register"]


class Agent:
    """Base class for pipeline agents.

    Subclasses define ``name``, ``role``, ``input_schema``, and
    ``output_schema`` and implement ``run(task)``.
    """

    name: ClassVar[str] = ""
    role: ClassVar[str] = ""
    input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    output_schema: ClassVar[dict[str, Any]] = {"type": "object"}

    @classmethod
    def validate_input(cls, task: dict[str, Any]) -> list[str]:
        return [error.message for error in jsonschema.Draft7Validator(cls.input_schema).iter_errors(task)]

    @classmethod
    def validate_output(cls, result: dict[str, Any]) -> list[str]:
        return [error.message for error in jsonschema.Draft7Validator(cls.output_schema).iter_errors(result)]

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """The stable A2A contract of this agent (for reports and future runtimes)."""
        return {
            "name": cls.name,
            "role": cls.role,
            "input_schema": cls.input_schema,
            "output_schema": cls.output_schema,
        }

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.name}.run() is not implemented")


#: Registry of all agents, keyed by name (the extension point for a future
#: distributed A2A runtime, which would discover agents through this map).
AGENTS: dict[str, type[Agent]] = {}


def register(agent_class: type[Agent]) -> type[Agent]:
    """Class decorator registering an agent under its declared name."""
    if not agent_class.name:
        raise ValueError("Agent classes must declare a non-empty name.")
    if agent_class.name in AGENTS:
        raise ValueError(f"Duplicate agent name: {agent_class.name}")
    AGENTS[agent_class.name] = agent_class
    return agent_class


def describe_agents() -> list[dict[str, Any]]:
    """The contract description of every registered agent (sorted by name)."""
    return [agent_class.describe() for _, agent_class in sorted(AGENTS.items())]
