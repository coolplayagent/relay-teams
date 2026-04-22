# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

from relay_teams.sessions.runs.todo_models import TodoItem, TodoSnapshot, TodoStatus
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.todo_tools.todo_read import register as register_todo_read
from relay_teams.tools.todo_tools.todo_write import register as register_todo_write


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            _ = description
            self.tools[func.__name__] = func
            return func

        return decorator


class _FakeTodoService:
    def __init__(self) -> None:
        self.replace_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    def get_for_run(self, *, run_id: str, session_id: str) -> TodoSnapshot:
        self.get_calls.append({"run_id": run_id, "session_id": session_id})
        return TodoSnapshot(
            run_id=run_id,
            session_id=session_id,
            items=(TodoItem(content="Inspect state", status=TodoStatus.IN_PROGRESS),),
            version=2,
        )

    def replace_for_run(
        self,
        *,
        run_id: str,
        session_id: str,
        items,
        updated_by_role_id: str | None = None,
        updated_by_instance_id: str | None = None,
    ) -> TodoSnapshot:
        self.replace_calls.append(
            {
                "run_id": run_id,
                "session_id": session_id,
                "items": tuple(items),
                "updated_by_role_id": updated_by_role_id,
                "updated_by_instance_id": updated_by_instance_id,
            }
        )
        return TodoSnapshot(
            run_id=run_id,
            session_id=session_id,
            items=tuple(items),
            version=3,
            updated_by_role_id=updated_by_role_id,
            updated_by_instance_id=updated_by_instance_id,
        )


async def _invoke_tool_action(
    action: Callable[..., object],
    raw_args: dict[str, object] | None,
) -> dict[str, object]:
    resolved_raw_args = {} if raw_args is None else raw_args
    tool_args = {
        name: resolved_raw_args[name]
        for name in inspect.signature(action).parameters
        if name in resolved_raw_args
    }
    result = action(**tool_args)
    if inspect.isawaitable(result):
        return await result
    return cast(dict[str, object], result)


@pytest.mark.asyncio
async def test_todo_write_calls_service_with_full_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    register_todo_write(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]], fake_agent.tools["todo_write"]
    )
    todo_service = _FakeTodoService()
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            todo_service=todo_service,
            run_id="run-1",
            session_id="session-1",
            role_id="MainAgent",
            instance_id="inst-1",
        ),
    )

    from relay_teams.tools.todo_tools import todo_write as todo_write_module

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[dict[str, object]]],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary
        return await _invoke_tool_action(action, raw_args)

    monkeypatch.setattr(todo_write_module, "execute_tool_call", _fake_execute_tool_call)

    result = await tool(
        ctx,
        items=[
            TodoItem(content="Inspect repo", status=TodoStatus.COMPLETED),
            TodoItem(content="Implement todo flow", status=TodoStatus.IN_PROGRESS),
        ],
    )

    assert todo_service.replace_calls == [
        {
            "run_id": "run-1",
            "session_id": "session-1",
            "items": (
                TodoItem(content="Inspect repo", status=TodoStatus.COMPLETED),
                TodoItem(
                    content="Implement todo flow",
                    status=TodoStatus.IN_PROGRESS,
                ),
            ),
            "updated_by_role_id": "MainAgent",
            "updated_by_instance_id": "inst-1",
        }
    ]
    todo_payload = cast(dict[str, object], result["todo"])
    assert todo_payload["version"] == 3


@pytest.mark.asyncio
async def test_todo_read_returns_current_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeAgent()
    register_todo_read(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]], fake_agent.tools["todo_read"]
    )
    todo_service = _FakeTodoService()
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            todo_service=todo_service,
            run_id="run-1",
            session_id="session-1",
        ),
    )

    from relay_teams.tools.todo_tools import todo_read as todo_read_module

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., object],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, raw_args
        return cast(dict[str, object], action())

    monkeypatch.setattr(todo_read_module, "execute_tool_call", _fake_execute_tool_call)

    result = await tool(ctx)

    assert todo_service.get_calls == [{"run_id": "run-1", "session_id": "session-1"}]
    todo_payload = cast(dict[str, object], result["todo"])
    assert todo_payload["items"] == [
        {"content": "Inspect state", "status": "in_progress"}
    ]
