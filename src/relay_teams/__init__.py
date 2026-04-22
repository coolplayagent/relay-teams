# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.interfaces.sdk.client import AgentTeamsClient

__all__ = ["AgentTeamsClient"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module 'relay_teams' has no attribute {name!r}")
    try:
        from relay_teams.interfaces.sdk.client import AgentTeamsClient
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.startswith("relay_teams"):
            raise
        raise ModuleNotFoundError(
            "relay_teams root package exports require SDK dependencies to be installed"
        ) from exc
    return AgentTeamsClient
