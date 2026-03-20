from __future__ import annotations

from typing import TYPE_CHECKING

from agent_teams_evals.backends.base import AgentBackend, AgentConfig, AgentEvent
from agent_teams_evals.backends.agent_teams_config import AgentTeamsConfig

if TYPE_CHECKING:
    from agent_teams_evals.backends.agent_teams import AgentTeamsBackend


def __getattr__(name: str):
    if name == "AgentTeamsBackend":
        from agent_teams_evals.backends.agent_teams import AgentTeamsBackend

        return AgentTeamsBackend
    raise AttributeError(name)


__all__ = [
    "AgentBackend",
    "AgentConfig",
    "AgentEvent",
    "AgentTeamsBackend",
    "AgentTeamsConfig",
]
