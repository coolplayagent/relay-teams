# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturn

from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.recoverable_invoker import RecoverableToolInvoker


def _deps() -> ToolDeps:
    return cast(
        ToolDeps,
        SimpleNamespace(
            run_id="run-1",
            session_id="session-1",
            role_registry=object(),
        ),
    )


def _registry(register: Callable[[Agent[ToolDeps, str]], None]) -> ToolRegistry:
    return ToolRegistry({"sample_tool": register})


@pytest.mark.asyncio
async def test_recoverable_invoker_returns_unavailable_error_for_missing_tool() -> None:
    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=ToolRegistry({}),
        deps=_deps(),
        tool_name="missing_tool",
        tool_call_id="call-1",
        raw_args={},
    )

    assert result["ok"] is False
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "tool_recovery_unavailable"


@pytest.mark.asyncio
async def test_recoverable_invoker_returns_unavailable_error_for_resolution_failure() -> (
    None
):
    def register(agent: Agent[ToolDeps, str]) -> None:
        if agent.__class__.__name__ == "_ToolFunctionCollector":
            raise RuntimeError("registry drift")

    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=_registry(register),
        deps=_deps(),
        tool_name="sample_tool",
        tool_call_id="call-1",
        raw_args={},
    )

    assert result["ok"] is False
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "tool_recovery_unavailable"
    assert error["message"] == "registry drift"


@pytest.mark.asyncio
async def test_recoverable_invoker_replays_sync_tool_with_json_string_args() -> None:
    def register(agent: Agent[ToolDeps, str]) -> None:
        @agent.tool()
        def sample_tool(ctx: object, value: str, count: int) -> dict[str, JsonValue]:
            assert getattr(ctx, "tool_call_id") == "call-1"
            return {"ok": True, "data": {"value": value, "count": count}}

    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=_registry(register),
        deps=_deps(),
        tool_name="sample_tool",
        tool_call_id="call-1",
        raw_args='{"value":"restored","count":3}',
    )

    assert result == {"ok": True, "data": {"value": "restored", "count": 3}}


@pytest.mark.asyncio
async def test_recoverable_invoker_normalizes_mapping_args_and_dict_result() -> None:
    def register(agent: Agent[ToolDeps, str]) -> None:
        @agent.tool()
        def sample_tool(ctx: object, **kwargs: JsonValue) -> dict[str, JsonValue]:
            _ = ctx
            return {"ok": True, "data": kwargs}

    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=_registry(register),
        deps=_deps(),
        tool_name="sample_tool",
        tool_call_id="call-1",
        raw_args={1: ("tuple", "value")},
    )

    assert result == {"ok": True, "data": {"1": ["tuple", "value"]}}


@pytest.mark.asyncio
async def test_recoverable_invoker_wraps_tool_return_values() -> None:
    def register(agent: Agent[ToolDeps, str]) -> None:
        @agent.tool()
        def sample_tool(ctx: object) -> ToolReturn:
            _ = ctx
            return ToolReturn(return_value=("subagent", "complete"))

    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=_registry(register),
        deps=_deps(),
        tool_name="sample_tool",
        tool_call_id="call-1",
        raw_args="not-json",
    )

    assert result == {"ok": True, "data": ["subagent", "complete"]}


@pytest.mark.asyncio
async def test_recoverable_invoker_preserves_tool_return_dict_values() -> None:
    def register(agent: Agent[ToolDeps, str]) -> None:
        @agent.tool()
        def sample_tool(ctx: object) -> ToolReturn:
            _ = ctx
            return ToolReturn(return_value={"ok": True, "data": {"done": True}})

    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=_registry(register),
        deps=_deps(),
        tool_name="sample_tool",
        tool_call_id="call-1",
        raw_args=[],
    )

    assert result == {"ok": True, "data": {"done": True}}


@pytest.mark.asyncio
async def test_recoverable_invoker_returns_tool_error_when_replay_raises() -> None:
    def register(agent: Agent[ToolDeps, str]) -> None:
        @agent.tool()
        async def sample_tool(ctx: object) -> dict[str, JsonValue]:
            _ = ctx
            raise OSError("boom")

    result = await RecoverableToolInvoker().invoke_async(
        tool_registry=_registry(register),
        deps=_deps(),
        tool_name="sample_tool",
        tool_call_id="call-1",
        raw_args={},
    )

    assert result["ok"] is False
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "tool_recovery_failed"
    assert error["message"] == "boom"
