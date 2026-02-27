from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps


ToolMount = Callable[[Agent[ToolDeps, str]], None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    mount: ToolMount
