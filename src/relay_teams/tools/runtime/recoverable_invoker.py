# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from typing import cast

from pydantic import JsonValue
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from relay_teams.sessions.runs.assistant_errors import build_tool_error_result
from relay_teams.tools.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.runtime.context import ToolDeps


class RecoverableToolInvoker:
    async def invoke_async(
        self,
        *,
        tool_registry: ToolRegistry,
        deps: ToolDeps,
        tool_name: str,
        tool_call_id: str,
        raw_args: object,
    ) -> dict[str, JsonValue]:
        try:
            function = self._resolve_tool_function(
                tool_registry=tool_registry,
                deps=deps,
                tool_name=tool_name,
            )
        except Exception as exc:
            return build_tool_error_result(
                error_code="tool_recovery_unavailable",
                message=str(exc) or exc.__class__.__name__,
            )
        if function is None:
            return build_tool_error_result(
                error_code="tool_recovery_unavailable",
                message=f"Tool is no longer available for recovery: {tool_name}",
            )
        args = self._tool_args(raw_args)
        ctx = RunContext(
            deps=deps,
            model=cast(Model, object()),
            usage=RunUsage(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=deps.run_id,
        )
        try:
            result = function(ctx, **args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return build_tool_error_result(
                error_code="tool_recovery_failed",
                message=str(exc) or exc.__class__.__name__,
            )
        if isinstance(result, ToolReturn):
            value = result.return_value
            if isinstance(value, dict):
                return _json_mapping(value)
            return {"ok": True, "data": _json_value(value)}
        if isinstance(result, dict):
            return _json_mapping(result)
        return {"ok": True, "data": _json_value(result)}

    @staticmethod
    def _resolve_tool_function(
        *,
        tool_registry: ToolRegistry,
        deps: ToolDeps,
        tool_name: str,
    ) -> Callable[..., object] | None:
        try:
            resolved = tool_registry.resolve_known(
                (tool_name,),
                context=ToolResolutionContext(session_id=deps.session_id),
                strict=False,
                consumer="tools.runtime.recoverable_invoker",
            )
        except AttributeError:
            return None
        if tool_name not in resolved:
            return None
        collector = _ToolFunctionCollector()
        registration_agent = cast(Agent[ToolDeps, str], cast(object, collector))
        setattr(collector, "_agent_teams_role_registry", deps.role_registry)
        for register in tool_registry.require(resolved):
            register(registration_agent)
        return collector.functions.get(tool_name)

    @staticmethod
    def _tool_args(raw_args: object) -> dict[str, JsonValue]:
        if isinstance(raw_args, str):
            try:
                decoded = json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
            if isinstance(decoded, dict):
                return _json_mapping(decoded)
            return {}
        if isinstance(raw_args, Mapping):
            return _json_mapping(raw_args)
        return {}


class _ToolFunctionCollector:
    def __init__(self) -> None:
        self.functions: dict[str, Callable[..., object]] = {}

    def tool(self, *, description: str | None = None) -> Callable[..., object]:
        _ = description

        def _decorator(func: object) -> object:
            if callable(func):
                self.functions[getattr(func, "__name__", "")] = func
            return func

        return _decorator


def _json_mapping(mapping: object) -> dict[str, JsonValue]:
    if not isinstance(mapping, Mapping):
        return {}
    normalized: dict[str, JsonValue] = {}
    for key, value in mapping.items():
        normalized[str(key)] = _json_value(value)
    return normalized


def _json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return _json_mapping(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return str(value)
