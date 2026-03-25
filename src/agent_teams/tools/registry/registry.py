# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent

if TYPE_CHECKING:
    from agent_teams.tools.runtime import ToolDeps

    ToolRegister: TypeAlias = Callable[[Agent[ToolDeps, str]], None]
else:
    ToolRegister = Callable[[Agent], None]


class ToolResolutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = ""


class ToolImplicitResolver(Protocol):
    def resolve_implicit_tools(
        self,
        context: ToolResolutionContext,
    ) -> tuple[str, ...]: ...


class ToolRegistry:
    def __init__(
        self,
        tools: dict[str, ToolRegister],
        *,
        hidden_from_config: tuple[str, ...] = (),
    ) -> None:
        self._tools = dict(tools)
        self._implicit_resolvers: list[ToolImplicitResolver] = []
        self._hidden_from_config = frozenset(hidden_from_config)

    def register_implicit_resolver(self, resolver: ToolImplicitResolver) -> None:
        self._implicit_resolvers.append(resolver)

    def require(
        self,
        names: tuple[str, ...],
        *,
        context: ToolResolutionContext | None = None,
    ) -> tuple[ToolRegister, ...]:
        resolved_names = self.resolve_names(names, context=context)
        missing = [name for name in resolved_names if name not in self._tools]
        if missing:
            raise ValueError(f"Unknown tools: {missing}")

        resolved: list[ToolRegister] = []
        seen: set[str] = set()
        for name in resolved_names:
            if name in seen:
                continue
            seen.add(name)
            resolved.append(self._tools[name])
        return tuple(resolved)

    def validate_known(self, names: tuple[str, ...]) -> None:
        self.require(names)

    def resolve_names(
        self,
        names: tuple[str, ...],
        *,
        context: ToolResolutionContext | None = None,
    ) -> tuple[str, ...]:
        resolved = list(names)
        if context is not None:
            for resolver in self._implicit_resolvers:
                resolved.extend(resolver.resolve_implicit_tools(context))
        return self._deduplicate_names(tuple(resolved))

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools.keys()))

    def list_configurable_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.list_names() if name not in self._hidden_from_config
        )

    def _deduplicate_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            deduplicated.append(name)
        return tuple(deduplicated)
