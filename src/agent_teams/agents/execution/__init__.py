# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.agents.execution.agent_builder import build_coordination_agent
    from agent_teams.agents.execution.message_repo import MessageRepository
    from agent_teams.agents.execution.subagent_runner import (
        SubAgentRequest,
        SubAgentRunner,
    )

__all__ = [
    "MessageRepository",
    "SubAgentRequest",
    "SubAgentRunner",
    "build_coordination_agent",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "MessageRepository": (
        "agent_teams.agents.execution.message_repo",
        "MessageRepository",
    ),
    "SubAgentRequest": (
        "agent_teams.agents.execution.subagent_runner",
        "SubAgentRequest",
    ),
    "SubAgentRunner": (
        "agent_teams.agents.execution.subagent_runner",
        "SubAgentRunner",
    ),
    "build_coordination_agent": (
        "agent_teams.agents.execution.agent_builder",
        "build_coordination_agent",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
