# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

import relay_teams.tools.workspace_tools as workspace_tools_module
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.models import (
    ToolExecutionError,
    ToolResultProjection,
)
from relay_teams.tools.runtime.models import ToolApprovalRequest
from relay_teams.tools.workspace_tools import (
    register_background_tasks,
    register_list_background_tasks,
    register_spawn_subagent,
    register_wait_background_task,
)
from relay_teams.tools.workspace_tools import shell as shell_module


async def _invoke_tool_action(
    action: Callable[..., Awaitable[ToolResultProjection]],
    raw_args: dict[str, object] | None,
) -> ToolResultProjection:
    resolved_raw_args = {} if raw_args is None else raw_args
    tool_args = {
        name: resolved_raw_args[name]
        for name in inspect.signature(action).parameters
        if name in resolved_raw_args
    }
    return await action(**tool_args)


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}
        self.tool_descriptions: dict[str, str] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            self.tool_descriptions[func.__name__] = description
            return func

        return decorator


class _FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.execution_root = root
        self.tmp_root = root / "tmp"
        self.ref = SimpleNamespace(workspace_id="workspace-1")

    def resolve_workdir(self, relative_path: str | None = None) -> Path:
        if relative_path is None:
            return self.execution_root
        return (self.execution_root / relative_path).resolve()


class _CapturingBackgroundTaskService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute_command(self, **kwargs: object):
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

    async def start_subagent(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        return BackgroundTaskRecord(
            background_task_id="subagent_123",
            run_id="run-1",
            session_id="session-1",
            kind=BackgroundTaskKind.SUBAGENT,
            instance_id="inst-1",
            role_id="writer",
            tool_call_id=cast(str | None, kwargs.get("tool_call_id")),
            title=str(kwargs["title"]),
            command="subagent:Crafter",
            cwd=str(kwargs["cwd"]),
            execution_mode="background",
            status=BackgroundTaskStatus.RUNNING,
            subagent_role_id=str(kwargs["subagent_role_id"]),
            subagent_run_id="subagent-run-1",
            subagent_task_id="task-1",
            subagent_instance_id="subagent-inst-1",
        )

    async def run_subagent(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            run_id="subagent-run-1",
            instance_id="subagent-inst-1",
            role_id=str(kwargs["subagent_role_id"]),
            task_id="task-1",
            title=str(kwargs["title"]),
            output="root cause found",
        )

    async def wait_for_run(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        return (
            BackgroundTaskRecord(
                background_task_id="subagent_123",
                run_id="run-1",
                session_id="session-1",
                kind=BackgroundTaskKind.SUBAGENT,
                instance_id="inst-1",
                role_id="writer",
                tool_call_id=cast(str | None, kwargs.get("tool_call_id")),
                title="Investigate test failures",
                command="subagent:Crafter",
                cwd="C:/workspace",
                execution_mode="background",
                status=BackgroundTaskStatus.COMPLETED,
                output_excerpt="root cause found",
                subagent_role_id="Crafter",
                subagent_run_id="subagent-run-1",
                subagent_task_id="task-1",
                subagent_instance_id="subagent-inst-1",
            ),
            True,
        )


class _RuntimeRoleResolverWithTemporaryRole:
    def __init__(self, role: RoleDefinition) -> None:
        self._role = role

    def get_temporary_role(self, *, run_id: str | None, role_id: str) -> RoleDefinition:
        _ = run_id
        if role_id == self._role.role_id:
            return self._role
        raise KeyError(f"Unknown temporary role: {role_id}")


class _RuntimeRoleResolverWithoutTemporaryRole:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_temporary_role(self, *, run_id: str | None, role_id: str) -> RoleDefinition:
        self.calls.append({"run_id": run_id, "role_id": role_id})
        raise KeyError(f"Unknown temporary role: {role_id}")


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
            shell_approval_repo=None,
            tool_approval_policy=SimpleNamespace(yolo=False),
            hook_runtime_env={},
        ),
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    monkeypatch.setattr(shell_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, command="pwd")

    assert service.calls[0]["tool_call_id"] is None
    assert result["background_task_id"] is None
    assert result["output"] == "/workspace\n"


@pytest.mark.asyncio
async def test_spawn_subagent_runs_synchronously_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["spawn_subagent"],
    )
    service = _CapturingBackgroundTaskService()
    workspace = _FakeWorkspace(tmp_path)
    role_registry = SimpleNamespace(resolve_subagent_role_id=lambda role_id: role_id)
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            role_registry=role_registry,
            runtime_role_resolver=None,
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
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    from relay_teams.tools.task_tools import spawn_subagent as spawn_subagent_module

    monkeypatch.setattr(spawn_subagent_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(
        ctx,
        role_id="Crafter",
        description="Investigate test failures",
        prompt="Inspect the failing tests and summarize the root cause.",
    )

    assert service.calls[0] == {
        "run_id": "run-1",
        "session_id": "session-1",
        "workspace_id": "workspace-1",
        "subagent_role_id": "Crafter",
        "subagent_role": None,
        "title": "Investigate test failures",
        "prompt": "Inspect the failing tests and summarize the root cause.",
    }
    assert result == {
        "completed": True,
        "output": "root cause found",
    }


@pytest.mark.asyncio
async def test_spawn_subagent_falls_back_to_static_role_when_temp_role_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["spawn_subagent"],
    )
    service = _CapturingBackgroundTaskService()
    workspace = _FakeWorkspace(tmp_path)
    runtime_role_resolver = _RuntimeRoleResolverWithoutTemporaryRole()
    role_registry = SimpleNamespace(resolve_subagent_role_id=lambda role_id: role_id)
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            role_registry=role_registry,
            runtime_role_resolver=runtime_role_resolver,
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
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    from relay_teams.tools.task_tools import spawn_subagent as spawn_subagent_module

    monkeypatch.setattr(spawn_subagent_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(
        ctx,
        role_id="Crafter",
        description="Investigate test failures",
        prompt="Inspect the failing tests and summarize the root cause.",
    )

    assert runtime_role_resolver.calls == [{"run_id": "run-1", "role_id": "Crafter"}]
    assert service.calls[0]["subagent_role_id"] == "Crafter"
    assert service.calls[0]["subagent_role"] is None
    assert result["output"] == "root cause found"


@pytest.mark.asyncio
async def test_spawn_subagent_passes_temporary_role_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["spawn_subagent"],
    )
    service = _CapturingBackgroundTaskService()
    workspace = _FakeWorkspace(tmp_path)
    role = RoleDefinition(
        role_id="skill_team_review_analyst_12345678",
        name="Analyst",
        description="Collects evidence.",
        version="1",
        mode=RoleMode.SUBAGENT,
        tools=(),
        system_prompt="Analyze.",
    )
    role_registry = SimpleNamespace(
        is_coordinator_role=lambda role_id: False,
        is_main_agent_role=lambda role_id: False,
    )
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            role_registry=role_registry,
            runtime_role_resolver=_RuntimeRoleResolverWithTemporaryRole(role),
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
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    from relay_teams.tools.task_tools import spawn_subagent as spawn_subagent_module

    monkeypatch.setattr(spawn_subagent_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(
        ctx,
        role_id=role.role_id,
        description="Investigate test failures",
        prompt="Inspect the failing tests and summarize the root cause.",
    )

    assert service.calls[0]["subagent_role_id"] == role.role_id
    assert service.calls[0]["subagent_role"] == role
    assert result["output"] == "root cause found"


@pytest.mark.asyncio
async def test_spawn_subagent_rejects_unspawnable_temporary_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["spawn_subagent"],
    )
    workspace = _FakeWorkspace(tmp_path)
    role = RoleDefinition(
        role_id="skill_team_review_analyst_12345678",
        name="Analyst",
        description="Collects evidence.",
        version="1",
        mode=RoleMode.PRIMARY,
        tools=(),
        system_prompt="Analyze.",
    )
    role_registry = SimpleNamespace(
        is_coordinator_role=lambda role_id: False,
        is_main_agent_role=lambda role_id: False,
    )
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=_CapturingBackgroundTaskService(),
            workspace=workspace,
            role_registry=role_registry,
            runtime_role_resolver=_RuntimeRoleResolverWithTemporaryRole(role),
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
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    from relay_teams.tools.task_tools import spawn_subagent as spawn_subagent_module

    monkeypatch.setattr(spawn_subagent_module, "execute_tool_call", _fake_execute_tool)

    with pytest.raises(ValueError, match="Role cannot be used as a subagent"):
        await tool(
            ctx,
            role_id=role.role_id,
            description="Investigate test failures",
            prompt="Inspect the failing tests and summarize the root cause.",
        )


@pytest.mark.asyncio
async def test_spawn_subagent_starts_background_subagent_task_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["spawn_subagent"],
    )
    service = _CapturingBackgroundTaskService()
    workspace = _FakeWorkspace(tmp_path)
    role_registry = SimpleNamespace(resolve_subagent_role_id=lambda role_id: role_id)
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            role_registry=role_registry,
            runtime_role_resolver=None,
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
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    from relay_teams.tools.task_tools import spawn_subagent as spawn_subagent_module

    monkeypatch.setattr(spawn_subagent_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(
        ctx,
        role_id="Crafter",
        description="Investigate test failures",
        prompt="Inspect the failing tests and summarize the root cause.",
        background=True,
    )

    assert service.calls[0]["subagent_role_id"] == "Crafter"
    assert service.calls[0]["tool_call_id"] == "call-1"
    assert result["background_task_id"] == "subagent_123"
    assert result["completed"] is False


@pytest.mark.asyncio
async def test_wait_background_task_waits_without_optional_timeout_argument(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_wait_background_task(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["wait_background_task"],
    )
    service = _CapturingBackgroundTaskService()
    workspace = _FakeWorkspace(tmp_path)
    ctx = SimpleNamespace(
        tool_call_id="call-2",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
        ),
    )
    captured_args: dict[str, object] = {}

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[ToolResultProjection]],
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request
        captured_args.update(args_summary)
        return cast(dict[str, object], (await action()).visible_data)

    from relay_teams.tools.workspace_tools import (
        wait_background_task as wait_background_task_module,
    )

    monkeypatch.setattr(wait_background_task_module, "execute_tool", _fake_execute_tool)

    result = await tool(ctx, background_task_id="subagent_123")

    assert service.calls[0] == {
        "run_id": "run-1",
        "background_task_id": "subagent_123",
    }
    assert captured_args == {"background_task_id": "subagent_123"}
    assert result["background_task_id"] == "subagent_123"
    assert result["completed"] is True


def test_build_shell_cache_key_includes_cwd_background_and_tty() -> None:
    running_key = shell_module.build_shell_cache_key(
        "bash -lc 'pwd'",
        workdir="one",
        tty=False,
        background=False,
    )
    different_cwd_key = shell_module.build_shell_cache_key(
        "pwd",
        workdir="two",
        tty=False,
        background=False,
    )
    different_tty_key = shell_module.build_shell_cache_key(
        "pwd",
        workdir="one",
        tty=True,
        background=False,
    )
    different_mode_key = shell_module.build_shell_cache_key(
        "pwd",
        workdir="one",
        tty=False,
        background=True,
    )

    assert running_key != different_cwd_key
    assert running_key != different_tty_key
    assert running_key != different_mode_key


@pytest.mark.asyncio
async def test_shell_builds_approval_request_after_resolving_workdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_background_tasks(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["shell"],
    )

    class _RecordingWorkspace(_FakeWorkspace):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.calls: list[str | None] = []

        def resolve_workdir(self, relative_path: str | None = None) -> Path:
            self.calls.append(relative_path)
            return super().resolve_workdir(relative_path)

    workspace = _RecordingWorkspace(tmp_path)
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            shell_approval_repo=None,
            tool_approval_policy=SimpleNamespace(yolo=False),
            hook_runtime_env={},
        ),
    )

    async def _fake_execute_tool(_ctx: object, **kwargs: object) -> dict[str, object]:
        del _ctx
        approval_request_factory = kwargs.get("approval_request_factory")
        assert callable(approval_request_factory)
        tool_input = cast(dict[str, object], kwargs.get("raw_args") or {})
        approval_request = cast(
            ToolApprovalRequest, approval_request_factory(tool_input)
        )
        assert approval_request.cache_key
        return {"delegated": True}

    monkeypatch.setattr(shell_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, command="pwd", workdir="../outside")

    assert result == {"delegated": True}
    assert workspace.calls == ["../outside", "../outside"]


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
        "spawn_subagent",
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


def test_register_spawn_subagent_only_registers_requested_tool(
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

    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))

    assert captured == ["spawn_subagent"]


def test_register_spawn_subagent_includes_subagent_capabilities_in_description() -> (
    None
):
    fake_agent = _FakeAgent()
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements requested changes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=("docs",),
            skills=("time",),
            mode=RoleMode.SUBAGENT,
            model_profile="default",
            system_prompt="You are a crafter.",
        )
    )
    setattr(fake_agent, "_agent_teams_role_registry", registry)

    register_spawn_subagent(cast(Agent[ToolDeps, str], fake_agent))

    description = fake_agent.tool_descriptions["spawn_subagent"]
    assert "Available Subagent Capabilities" in description
    assert "### Crafter" in description
    assert "- Tools: read, write" in description
    assert "- MCP Servers: docs" in description
    assert "- Skills: time" in description


@pytest.mark.asyncio
async def test_shell_returns_blocked_tool_result_for_local_policy_command(
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
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            shell_approval_repo=None,
            tool_approval_policy=SimpleNamespace(yolo=True),
            hook_runtime_env={},
        ),
    )

    async def _fake_execute_tool(
        _ctx: object,
        *,
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del approval_request
        tool_input = cast(dict[str, object], raw_args or {})
        assert callable(approval_request_factory)
        approval_request = cast(
            ToolApprovalRequest,
            approval_request_factory(tool_input),
        )
        assert approval_request is not None
        assert approval_request.source == "shell_local_policy"
        try:
            await _invoke_tool_action(action, tool_input)
        except ToolExecutionError as exc:
            return {"ok": False, "error": {"type": exc.error_type, "message": str(exc)}}
        raise AssertionError("expected local shell policy to block action")

    monkeypatch.setattr(shell_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, command="curl https://example.com")

    assert result == {
        "ok": False,
        "error": {
            "type": "tool_blocked",
            "message": "command is blocked by local shell policy: curl",
        },
    }
    assert service.calls == []


@pytest.mark.asyncio
async def test_shell_yolo_blocks_parent_directory_change_in_command(
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
        tool_call_id="call-1",
        deps=SimpleNamespace(
            background_task_service=service,
            workspace=workspace,
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            shell_approval_repo=None,
            tool_approval_policy=SimpleNamespace(yolo=True),
            hook_runtime_env={},
        ),
    )

    async def _fake_execute_tool(
        _ctx: object,
        *,
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del approval_request
        tool_input = cast(dict[str, object], raw_args or {})
        assert callable(approval_request_factory)
        approval_request = cast(
            ToolApprovalRequest,
            approval_request_factory(tool_input),
        )
        assert approval_request is not None
        assert approval_request.source == "shell_local_policy"
        try:
            await _invoke_tool_action(action, tool_input)
        except ToolExecutionError as exc:
            return {"ok": False, "error": {"type": exc.error_type, "message": str(exc)}}
        raise AssertionError("expected yolo directory change to block action")

    monkeypatch.setattr(shell_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, command="cd .. && pwd")

    assert result == {
        "ok": False,
        "error": {
            "type": "tool_blocked",
            "message": (
                "directory change is blocked by local shell policy: "
                f"{tmp_path.parent.resolve()} is outside {tmp_path.resolve()}"
            ),
        },
    }
    assert service.calls == []


@pytest.mark.asyncio
async def test_shell_returns_blocked_tool_result_for_workdir_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_background_tasks(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["shell"],
    )

    class _BlockedWorkspace(_FakeWorkspace):
        def resolve_workdir(self, relative_path: str | None = None) -> Path:
            raise ValueError(f"Path is outside workspace write scope: {relative_path}")

    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            workspace=_BlockedWorkspace(tmp_path),
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="writer",
            shell_approval_repo=None,
            tool_approval_policy=SimpleNamespace(yolo=True),
            hook_runtime_env={},
        ),
    )

    async def _fake_execute_tool(
        _ctx: object,
        *,
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del approval_request
        tool_input = cast(dict[str, object], raw_args or {})
        assert callable(approval_request_factory)
        approval_request = cast(
            ToolApprovalRequest,
            approval_request_factory(tool_input),
        )
        assert approval_request is not None
        assert approval_request.source == "shell_local_policy"
        try:
            await _invoke_tool_action(action, tool_input)
        except ToolExecutionError as exc:
            return {"ok": False, "error": {"type": exc.error_type, "message": str(exc)}}
        raise AssertionError("expected workdir validation to block action")

    monkeypatch.setattr(shell_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, command="pwd", workdir="../outside")

    assert result == {
        "ok": False,
        "error": {
            "type": "tool_blocked",
            "message": "Path is outside workspace write scope: ../outside",
        },
    }
