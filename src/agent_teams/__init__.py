# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.interfaces.sdk.client import AgentTeamsApp, AgentTeamsClient

__all__ = ["AgentTeamsApp", "AgentTeamsClient"]
_SDK_IMPORT_ERROR: ModuleNotFoundError | None = None

try:
    from agent_teams.interfaces.sdk.client import AgentTeamsApp, AgentTeamsClient
except ModuleNotFoundError as exc:
    if exc.name is not None and exc.name.startswith("agent_teams"):
        raise
    _SDK_IMPORT_ERROR = exc


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module 'agent_teams' has no attribute {name!r}")
    if _SDK_IMPORT_ERROR is not None:
        raise ModuleNotFoundError(
            "agent_teams root package exports require SDK dependencies to be installed"
        ) from _SDK_IMPORT_ERROR
    raise AttributeError(f"module 'agent_teams' has no attribute {name!r}")
