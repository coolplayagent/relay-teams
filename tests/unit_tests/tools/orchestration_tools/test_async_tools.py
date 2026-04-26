# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.agents.orchestration.task_contracts import TaskUpdate
from relay_teams.tools.orchestration_tools import (
    list_delegated_tasks,
    list_run_tasks,
    update_task,
)
from relay_teams.tools.runtime.context import ToolContext, ToolDeps

RegisteredTool = Callable[..., Awaitable[dict[str, JsonValue]]]


class _ToolCaptureAgent:
    def __init__(self) -> None:
        self.registered: RegisteredTool | None = None

    def tool(self, *, description: str) -> Callable[[RegisteredTool], RegisteredTool]:
        assert description

        def _decorator(func: RegisteredTool) -> RegisteredTool:
            self.registered = func
            return func

        return _decorator


class _FakeTaskService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_update: TaskUpdate | None = None

    async def list_delegated_tasks_async(
        self, *, run_id: str, include_root: bool
    ) -> dict[str, JsonValue]:
        self.calls.append(f"delegated:{run_id}:{include_root}")
        return {"kind": "delegated"}

    async def list_run_tasks_async(
        self, *, run_id: str, include_root: bool
    ) -> dict[str, JsonValue]:
        self.calls.append(f"run:{run_id}:{include_root}")
        return {"kind": "run"}

    async def update_task_async(
        self, *, run_id: str, task_id: str, update: TaskUpdate
    ) -> dict[str, JsonValue]:
        self.calls.append(f"update:{run_id}:{task_id}")
        self.last_update = update
        return {"kind": "updated"}


class _FakeDeps:
    def __init__(self, task_service: _FakeTaskService) -> None:
        self.run_id = "run-1"
        self.task_service = task_service


class _FakeContext:
    def __init__(self, task_service: _FakeTaskService) -> None:
        self.deps = _FakeDeps(task_service)


@pytest.mark.asyncio
async def test_list_delegated_tasks_tool_uses_async_task_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _execute_tool(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[dict[str, JsonValue]]],
    ) -> dict[str, JsonValue]:
        _ = (ctx, tool_name, args_summary)
        return await action()

    monkeypatch.setattr(list_delegated_tasks, "execute_tool", _execute_tool)
    agent = _ToolCaptureAgent()
    list_delegated_tasks.register(cast(Agent[ToolDeps, str], agent))
    assert agent.registered is not None
    task_service = _FakeTaskService()

    result = await agent.registered(
        cast(ToolContext, _FakeContext(task_service)),
        include_root=True,
    )

    assert result == {"kind": "delegated"}
    assert task_service.calls == ["delegated:run-1:True"]


@pytest.mark.asyncio
async def test_list_run_tasks_tool_uses_async_task_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _execute_tool(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[dict[str, JsonValue]]],
    ) -> dict[str, JsonValue]:
        _ = (ctx, tool_name, args_summary)
        return await action()

    monkeypatch.setattr(list_run_tasks, "execute_tool", _execute_tool)
    agent = _ToolCaptureAgent()
    list_run_tasks.register(cast(Agent[ToolDeps, str], agent))
    assert agent.registered is not None
    task_service = _FakeTaskService()

    result = await agent.registered(
        cast(ToolContext, _FakeContext(task_service)),
        include_root=True,
    )

    assert result == {"kind": "run"}
    assert task_service.calls == ["run:run-1:True"]


@pytest.mark.asyncio
async def test_update_task_tool_uses_async_task_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _execute_tool_call(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[dict[str, JsonValue]]],
        raw_args: dict[str, object],
    ) -> dict[str, JsonValue]:
        _ = (ctx, tool_name, args_summary)
        return await action(
            task_id=cast(str, raw_args["task_id"]),
            objective=cast(str | None, raw_args["objective"]),
            title=cast(str | None, raw_args["title"]),
        )

    monkeypatch.setattr(update_task, "execute_tool_call", _execute_tool_call)
    agent = _ToolCaptureAgent()
    update_task.register(cast(Agent[ToolDeps, str], agent))
    assert agent.registered is not None
    task_service = _FakeTaskService()

    result = await agent.registered(
        cast(ToolContext, _FakeContext(task_service)),
        task_id="task-1",
        objective="new objective",
        title="New title",
    )

    assert result == {"kind": "updated"}
    assert task_service.calls == ["update:run-1:task-1"]
    assert task_service.last_update == TaskUpdate(
        objective="new objective",
        title="New title",
    )
