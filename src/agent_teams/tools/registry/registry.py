# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Agent

from agent_teams.logger import get_logger, log_event

if TYPE_CHECKING:
    from agent_teams.tools.runtime import ToolDeps

    ToolRegister: TypeAlias = Callable[[Agent[ToolDeps, str]], None]
else:
    ToolRegister = Callable[[Agent], None]


LOGGER = get_logger(__name__)


class ToolResolutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = ""


class ToolAvailabilityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    error_type: str
    message: str


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
        legacy_aliases: dict[str, str] | None = None,
    ) -> None:
        self._tools: dict[str, ToolRegister] = {}
        self._unavailable_tools: dict[str, ToolAvailabilityRecord] = {}
        for name, register in tools.items():
            availability = _probe_tool_availability(name=name, register=register)
            if availability is not None:
                self._unavailable_tools[name] = availability
                continue
            self._tools[name] = register
        self._implicit_resolvers: list[ToolImplicitResolver] = []
        self._hidden_from_config = frozenset(hidden_from_config)
        self._legacy_aliases = (
            {}
            if legacy_aliases is None
            else {str(key): str(value) for key, value in legacy_aliases.items()}
        )

    def register_implicit_resolver(self, resolver: ToolImplicitResolver) -> None:
        self._implicit_resolvers.append(resolver)

    def require(
        self,
        names: tuple[str, ...],
        *,
        context: ToolResolutionContext | None = None,
    ) -> tuple[ToolRegister, ...]:
        resolved_names = self.resolve_known(names, context=context)
        resolved: list[ToolRegister] = []
        for name in resolved_names:
            resolved.append(self._tools[name])
        return tuple(resolved)

    def validate_known(self, names: tuple[str, ...]) -> None:
        _ = self.resolve_known(names)

    def resolve_known(
        self,
        names: tuple[str, ...],
        *,
        context: ToolResolutionContext | None = None,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        resolved_names = self.resolve_names(names, context=context)
        if not strict:
            resolved_names = tuple(
                self._legacy_aliases.get(name, name) for name in resolved_names
            )
            resolved_names = self._deduplicate_names(resolved_names)
        known_names = tuple(name for name in resolved_names if name in self._tools)
        unavailable_names = tuple(
            name for name in resolved_names if name in self._unavailable_tools
        )
        missing_names = tuple(
            name
            for name in resolved_names
            if name not in self._tools and name not in self._unavailable_tools
        )
        if strict and (missing_names or unavailable_names):
            errors: list[str] = []
            if missing_names:
                errors.append(f"Unknown tools: {list(missing_names)}")
            if unavailable_names:
                errors.append(f"Unavailable tools: {list(unavailable_names)}")
            raise ValueError("; ".join(errors))
        if missing_names:
            payload: dict[str, JsonValue] = {
                "requested_tool_names": list(names),
                "resolved_tool_names": list(known_names),
                "ignored_tool_names": list(missing_names),
            }
            if context is not None:
                payload["context"] = context.model_dump(mode="json")
            if consumer is not None:
                payload["consumer"] = consumer
            log_event(
                LOGGER,
                logging.WARNING,
                event="tools.registry.unknown_ignored",
                message="Ignoring unknown tools from existing configuration",
                payload=payload,
            )
        if unavailable_names:
            payload = {
                "requested_tool_names": list(names),
                "resolved_tool_names": list(known_names),
                "ignored_tool_names": list(unavailable_names),
                "unavailable_tools": [
                    self._unavailable_tools[name].model_dump(mode="json")
                    for name in unavailable_names
                ],
            }
            if context is not None:
                payload["context"] = context.model_dump(mode="json")
            if consumer is not None:
                payload["consumer"] = consumer
            log_event(
                LOGGER,
                logging.WARNING,
                event="tools.registry.unavailable_ignored",
                message="Ignoring unavailable tools from existing configuration",
                payload=payload,
            )
        return known_names

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

    def list_unavailable_tools(self) -> tuple[ToolAvailabilityRecord, ...]:
        return tuple(
            self._unavailable_tools[name]
            for name in sorted(self._unavailable_tools.keys())
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


class _RegistrationProbeAgent:
    def tool(self, *, description: str | None = None):
        _ = description

        def _decorator(func: object) -> object:
            return func

        return _decorator


def _probe_tool_availability(
    *,
    name: str,
    register: ToolRegister,
) -> ToolAvailabilityRecord | None:
    try:
        register(cast("Agent[ToolDeps, str]", _RegistrationProbeAgent()))
    except Exception as exc:
        return ToolAvailabilityRecord(
            name=name,
            error_type=type(exc).__name__,
            message=str(exc),
        )
    return None
