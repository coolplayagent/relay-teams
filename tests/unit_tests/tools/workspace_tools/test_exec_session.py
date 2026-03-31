# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

from agent_teams.computer import ComputerActionRisk
from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionRecord,
    ExecSessionStatus,
)
from agent_teams.tools.runtime import (
    ToolApprovalRequest,
    ToolDeps,
    ToolResultProjection,
)
from agent_teams.tools.workspace_tools import register_exec_session
from agent_teams.tools.workspace_tools import exec_session as exec_session_module


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


class _CapturingExecSessionManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def exec_command(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        record = ExecSessionRecord(
            exec_session_id="exec_123",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=cast(str | None, kwargs.get("tool_call_id")),
            command=str(kwargs["command"]),
            cwd=str(kwargs["cwd"]),
            status=ExecSessionStatus.RUNNING,
        )
        return record, False

    async def interact_for_run(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        record = ExecSessionRecord(
            exec_session_id=str(kwargs["exec_session_id"]),
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            command="bash",
            cwd="/workspace",
            status=ExecSessionStatus.RUNNING,
        )
        return record, False


@pytest.mark.asyncio
async def test_exec_command_passes_none_tool_call_id_without_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_exec_session(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["exec_command"],
    )
    manager = _CapturingExecSessionManager()
    workspace = _FakeWorkspace(tmp_path)
    ctx = SimpleNamespace(
        tool_call_id=None,
        deps=SimpleNamespace(
            exec_session_manager=manager,
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

    monkeypatch.setattr(exec_session_module, "execute_tool", _fake_execute_tool)

    result = await tool(ctx, command="pwd")

    assert manager.calls[0]["tool_call_id"] is None
    assert isinstance(result, dict)
    assert result["exec_session_id"] == "exec_123"


def test_build_exec_command_cache_key_includes_cwd_and_tty() -> None:
    running_key = exec_session_module._build_exec_command_cache_key(
        "bash -lc 'pwd'",
        cwd=Path("/workspace/one"),
        tty=False,
    )
    different_cwd_key = exec_session_module._build_exec_command_cache_key(
        "pwd",
        cwd=Path("/workspace/two"),
        tty=False,
    )
    different_tty_key = exec_session_module._build_exec_command_cache_key(
        "pwd",
        cwd=Path("/workspace/one"),
        tty=True,
    )

    assert running_key != different_cwd_key
    assert running_key != different_tty_key


def test_register_exec_session_is_idempotent_per_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def _fake_register(agent: object) -> None:
        calls.append(agent)

    monkeypatch.setattr(exec_session_module, "register", _fake_register)
    fake_agent = _FakeAgent()

    register_exec_session(cast(Agent[ToolDeps, str], fake_agent))
    register_exec_session(cast(Agent[ToolDeps, str], fake_agent))

    assert calls == [fake_agent]


@pytest.mark.asyncio
async def test_write_stdin_requests_approval_for_non_empty_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_exec_session(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write_stdin"],
    )
    manager = _CapturingExecSessionManager()
    workspace = _FakeWorkspace(tmp_path)
    ctx = SimpleNamespace(
        tool_call_id=None,
        deps=SimpleNamespace(
            exec_session_manager=manager,
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
        ),
    )
    captured: dict[str, object] = {}

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[ToolResultProjection]],
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, action
        captured["args_summary"] = args_summary
        captured["approval_request"] = approval_request
        return {"ok": True}

    monkeypatch.setattr(exec_session_module, "execute_tool", _fake_execute_tool)

    result = await tool(
        ctx,
        exec_session_id="exec_123",
        chars="echo approved\n",
    )

    approval_request = cast(
        ToolApprovalRequest,
        captured["approval_request"],
    )
    assert result == {"ok": True}
    assert approval_request is not None
    assert approval_request.risk_level == ComputerActionRisk.GUARDED
    assert approval_request.target_summary == "stdin for exec session exec_123"
    assert "chars_sha256=" in approval_request.cache_key
    assert cast(dict[str, object], captured["args_summary"])["chars_preview"] == (
        "echo approved\n"
    )


@pytest.mark.asyncio
async def test_write_stdin_skips_approval_for_empty_poll(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_exec_session(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write_stdin"],
    )
    manager = _CapturingExecSessionManager()
    workspace = _FakeWorkspace(tmp_path)
    ctx = SimpleNamespace(
        tool_call_id=None,
        deps=SimpleNamespace(
            exec_session_manager=manager,
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
        ),
    )
    captured: dict[str, object] = {}

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[ToolResultProjection]],
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, action
        captured["args_summary"] = args_summary
        captured["approval_request"] = approval_request
        return {"ok": True}

    monkeypatch.setattr(exec_session_module, "execute_tool", _fake_execute_tool)

    result = await tool(ctx, exec_session_id="exec_123")

    assert result == {"ok": True}
    assert captured["approval_request"] is None
    assert cast(dict[str, object], captured["args_summary"])["chars_preview"] == ""
