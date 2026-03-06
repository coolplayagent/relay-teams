# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps

ToolRegister = Callable[[Agent[ToolDeps, str]], None]


class ToolRegistry:
    def __init__(self, tools: dict[str, ToolRegister]) -> None:
        self._tools = dict(tools)

    def require(self, names: tuple[str, ...]) -> tuple[ToolRegister, ...]:
        missing = [name for name in names if name not in self._tools]
        if missing:
            raise ValueError(f"Unknown tools: {missing}")

        resolved: list[ToolRegister] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            resolved.append(self._tools[name])
        return tuple(resolved)

    def validate_known(self, names: tuple[str, ...]) -> None:
        self.require(names)

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools.keys()))
