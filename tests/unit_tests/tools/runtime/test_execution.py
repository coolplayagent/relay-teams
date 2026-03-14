# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import cast

from agent_teams.sessions.runs.enums import ApprovalMode
from agent_teams.notifications import NotificationService, default_notification_config
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub

from agent_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
)
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.tools.runtime import ToolApprovalPolicy, ToolContext, execute_tool


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeApprovalManager:
    def __init__(
        self,
        wait_result: tuple[str, str] | None = None,
        timeout: bool = False,
    ) -> None:
        self.wait_result = wait_result
        self.timeout = timeout
        self.last_open: dict[str, object] | None = None

    def open_approval(self, **kwargs) -> None:
        self.last_open = kwargs

    def get_approval(self, **kwargs):
        _ = kwargs
        return None

    def wait_for_approval(self, **kwargs):
        if self.timeout:
            raise TimeoutError("timeout")
        return self.wait_result or ("approve", "")

    def close_approval(self, **kwargs) -> None:
        _ = kwargs


@dataclass(frozen=True)
class _FakePolicy:
    needs_approval: bool
    timeout_seconds: float = 0.01

    def requires_approval(self, tool_name: str) -> bool:
        _ = tool_name
        return self.needs_approval


class _FakeDeps:
    def __init__(
        self,
        *,
        manager: _FakeApprovalManager,
        policy: _FakePolicy | ToolApprovalPolicy,
    ) -> None:
        db_path = Path(mkdtemp()) / "runtime.db"
        self.run_id = "run-1"
        self.trace_id = "trace-1"
        self.task_id = "task-1"
        self.session_id = "session-1"
        self.instance_id = "inst-1"
        self.role_id = "spec_coder"
        self.role_registry = RoleRegistry()
        self.role_registry.register(
            RoleDefinition(
                role_id="spec_coder",
                name="Spec Coder",
                description="Implements requested changes.",
                version="1",
                tools=(),
                system_prompt="Implement tasks.",
            )
        )
        self.run_event_hub = _FakeRunEventHub()
        self.run_control_manager = _FakeRunControlManager()
        self.tool_approval_manager = manager
        self.tool_approval_policy = policy
        self.notification_service = _build_notification_service(self.run_event_hub)
        self.approval_ticket_repo = ApprovalTicketRepository(db_path)
        self.run_runtime_repo = RunRuntimeRepository(db_path)
        self.run_runtime_repo.ensure(
            run_id=self.run_id,
            session_id=self.session_id,
            root_task_id=self.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
        )


class _FakeCtx:
    def __init__(self, deps: _FakeDeps) -> None:
        self.deps = deps
        self.tool_call_id: str | None = None
        self.retry: int = 0


class _FakeRunControlManager:
    def is_run_stop_requested(self, run_id: str) -> bool:
        _ = run_id
        return False

    def is_subagent_stop_requested(self, *, run_id: str, instance_id: str) -> bool:
        _ = (run_id, instance_id)
        return False

    def raise_if_cancelled(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
    ) -> None:
        _ = (run_id, instance_id)


def _build_notification_service(
    run_event_hub: _FakeRunEventHub,
) -> NotificationService:
    return NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, run_event_hub)),
        get_config=default_notification_config,
    )


def test_execute_tool_returns_standard_envelope() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: "hello",
        )
    )
    meta = cast(dict[str, JsonValue], result["meta"])
    runtime = deps.run_runtime_repo.get(deps.run_id)
    assert result["ok"] is True
    assert result["tool"] == "read"
    assert result["data"] == "hello"
    assert result["error"] is None
    assert meta["approval_required"] is False
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.RUNNING
    assert runtime.phase == RunRuntimePhase.SUBAGENT_RUNNING


def test_execute_tool_skips_approval_flow_in_yolo_mode() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=ToolApprovalPolicy(
            approval_mode=ApprovalMode.YOLO,
            timeout_seconds=0.01,
        ),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-yolo"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="shell",
            args_summary={"command": "pwd"},
            action=lambda: {"stdout": "/tmp"},
        )
    )

    meta = cast(dict[str, JsonValue], result["meta"])
    assert result["ok"] is True
    assert result["data"] == {"stdout": "/tmp"}
    assert meta["approval_required"] is False
    assert meta["approval_status"] == "not_required"
    assert deps.approval_ticket_repo.get("call-model-yolo") is None
    assert manager.last_open is None
    assert not any(
        event.event_type == RunEventType.TOOL_APPROVAL_REQUESTED
        for event in deps.run_event_hub.events
    )


def test_execute_tool_returns_denied_error_when_approval_rejected() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("deny", "not safe")),
        policy=_FakePolicy(needs_approval=True),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-deny"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=lambda: "should_not_run",
        )
    )
    error = cast(dict[str, JsonValue], result["error"])
    meta = cast(dict[str, JsonValue], result["meta"])
    ticket = deps.approval_ticket_repo.get("call-model-deny")
    assert result["ok"] is False
    assert error["type"] == "approval_denied"
    assert meta["approval_required"] is True
    assert meta["approval_status"] == "deny"
    assert any(
        event.event_type == RunEventType.TOOL_APPROVAL_REQUESTED
        for event in deps.run_event_hub.events
    )
    assert any(
        event.event_type == RunEventType.TOOL_APPROVAL_RESOLVED
        for event in deps.run_event_hub.events
    )
    assert any(
        event.event_type == RunEventType.NOTIFICATION_REQUESTED
        for event in deps.run_event_hub.events
    )
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.DENIED


def test_execute_tool_returns_timeout_error_when_approval_times_out() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(timeout=True),
        policy=_FakePolicy(needs_approval=True, timeout_seconds=0.01),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-123"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="shell",
            args_summary={"command": "echo hi"},
            action=lambda: "should_not_run",
        )
    )
    error = cast(dict[str, JsonValue], result["error"])
    meta = cast(dict[str, JsonValue], result["meta"])
    ticket = deps.approval_ticket_repo.get("call-model-123")
    assert result["ok"] is False
    assert error["type"] == "approval_timeout"
    assert meta["approval_status"] == "timeout"
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.TIMED_OUT


def test_execute_tool_approval_uses_model_tool_call_id_when_present() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=_FakePolicy(needs_approval=True),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-123"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=lambda: "ok",
        )
    )
    ticket = deps.approval_ticket_repo.get("call-model-123")
    assert result["ok"] is True
    assert manager.last_open is not None
    assert manager.last_open["tool_call_id"] == "call-model-123"
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.COMPLETED


def test_execute_tool_reuses_approved_ticket_without_reopening_request() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=_FakePolicy(needs_approval=True),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-123"
    deps.approval_ticket_repo.upsert_requested(
        tool_call_id="call-model-123",
        run_id=deps.run_id,
        session_id=deps.session_id,
        task_id=deps.task_id,
        instance_id=deps.instance_id,
        role_id=deps.role_id,
        tool_name="write",
        args_preview='{"path": "a.txt"}',
    )
    deps.approval_ticket_repo.resolve(
        tool_call_id="call-model-123",
        status=ApprovalTicketStatus.APPROVED,
    )

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=lambda: "fresh",
        )
    )

    ticket = deps.approval_ticket_repo.get("call-model-123")
    assert result["ok"] is True
    assert result["data"] == "fresh"
    assert manager.last_open is None
    assert not any(
        event.event_type == RunEventType.TOOL_APPROVAL_REQUESTED
        for event in deps.run_event_hub.events
    )
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.COMPLETED


def test_execute_tool_republishes_requested_ticket_when_reopened() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=_FakePolicy(needs_approval=True),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-123"
    deps.approval_ticket_repo.upsert_requested(
        tool_call_id="call-model-123",
        run_id=deps.run_id,
        session_id=deps.session_id,
        task_id=deps.task_id,
        instance_id=deps.instance_id,
        role_id=deps.role_id,
        tool_name="write",
        args_preview='{"path": "a.txt"}',
    )

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=lambda: "fresh",
        )
    )

    ticket = deps.approval_ticket_repo.get("call-model-123")
    assert result["ok"] is True
    assert result["data"] == "fresh"
    assert manager.last_open is not None
    assert any(
        event.event_type == RunEventType.TOOL_APPROVAL_REQUESTED
        for event in deps.run_event_hub.events
    )
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.COMPLETED
