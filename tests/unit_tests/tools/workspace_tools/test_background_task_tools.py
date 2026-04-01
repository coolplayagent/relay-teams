# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

import agent_teams.tools.workspace_tools as workspace_tools_module
from agent_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from agent_teams.tools.runtime import ToolDeps, ToolResultProjection
from agent_teams.tools.workspace_tools import (
    register_background_tasks,
    register_list_background_tasks,
)
from agent_teams.tools.workspace_tools import shell as shell_module


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        del description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


class _FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.execution_root = root
        self.tmp_root = root / "tmp"

    def resolve_workdir(self, relative_path: str | None = None) -> Path:
        if relative_path is None:
            return self.execution_root
        return (self.execution_root / relative_path).resolve()


class _CapturingBackgroundTaskService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_shell(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        record = BackgroundTaskRecord(
            background_task_id="exec_123",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=cast(str | None, kwargs.get("tool_call_id")),
            command=str(kwargs["command"]),
            cwd=str(kwargs["cwd"]),
            execution_mode=(
                "background" if bool(kwargs.get("background")) else "foreground"
            ),
            status=BackgroundTaskStatus.COMPLETED,
            output_excerpt="/workspace\n",
        )
        return record, True


@pytest.mark.asyncio
async def test_shell_passes_none_tool_call_id_without_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_background_tasks(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["shell"],
    )
    service = _CapturingBackgroundTaskService()
    workspace = _FakeWorkspace(tmp_path)
    ctx = SimpleNamespace(
        tool_call_id=None,
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
        ),
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[ToolResultProjection]],
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(dict[str, object], (await action()).visible_data)

    monkeypatch.setattr(shell_module, "execute_tool", _fake_execute_tool)

    result = await tool(ctx, command="pwd")

    assert service.calls[0]["tool_call_id"] is None
    assert result["background_task_id"] is None
    assert result["output"] == "/workspace\n"


def test_build_shell_cache_key_includes_cwd_background_and_tty() -> None:
    running_key = shell_module.build_shell_cache_key(
        "bash -lc 'pwd'",
        cwd=Path("/workspace/one"),
        tty=False,
        background=False,
    )
    different_cwd_key = shell_module.build_shell_cache_key(
        "pwd",
        cwd=Path("/workspace/two"),
        tty=False,
        background=False,
    )
    different_tty_key = shell_module.build_shell_cache_key(
        "pwd",
        cwd=Path("/workspace/one"),
        tty=True,
        background=False,
    )
    different_mode_key = shell_module.build_shell_cache_key(
        "pwd",
        cwd=Path("/workspace/one"),
        tty=False,
        background=True,
    )

    assert running_key != different_cwd_key
    assert running_key != different_tty_key
    assert running_key != different_mode_key


def test_register_background_tasks_is_idempotent_per_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _fake_register_single_tool(agent: object, tool_name: str) -> None:
        _ = agent
        captured.append(tool_name)

    monkeypatch.setattr(
        workspace_tools_module,
        "_register_single_tool",
        _fake_register_single_tool,
    )
    fake_agent = _FakeAgent()

    register_background_tasks(cast(Agent[ToolDeps, str], fake_agent))
    register_background_tasks(cast(Agent[ToolDeps, str], fake_agent))

    assert captured == [
        "shell",
        "list_background_tasks",
        "wait_background_task",
        "stop_background_task",
    ]


def test_register_list_background_tasks_only_registers_requested_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _fake_register_single_tool(agent: object, tool_name: str) -> None:
        _ = agent
        captured.append(tool_name)

    monkeypatch.setattr(
        workspace_tools_module,
        "_register_single_tool",
        _fake_register_single_tool,
    )
    fake_agent = _FakeAgent()

    register_list_background_tasks(cast(Agent[ToolDeps, str], fake_agent))

    assert captured == ["list_background_tasks"]
