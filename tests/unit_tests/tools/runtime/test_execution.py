# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from agent_teams.notifications import NotificationService, default_notification_config
from agent_teams.runs.enums import RunEventType
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, execute_tool


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

    def wait_for_approval(self, **kwargs):
        if self.timeout:
            raise TimeoutError("timeout")
        return self.wait_result or ("approve", "")

    def close_approval(self, **kwargs) -> None:
        return None


@dataclass(frozen=True)
class _FakePolicy:
    needs_approval: bool
    timeout_seconds: float = 0.01

    def requires_approval(self, tool_name: str) -> bool:
        return self.needs_approval


class _FakeDeps:
    def __init__(self, *, manager: _FakeApprovalManager, policy: _FakePolicy) -> None:
        self.run_id = "run-1"
        self.trace_id = "trace-1"
        self.task_id = "task-1"
        self.session_id = "session-1"
        self.instance_id = "inst-1"
        self.role_id = "spec_coder"
        self.run_event_hub = _FakeRunEventHub()
        self.run_control_manager = _FakeRunControlManager()
        self.tool_approval_manager = manager
        self.tool_approval_policy = policy
        self.notification_service = _build_notification_service(self.run_event_hub)


class _FakeCtx:
    def __init__(self, deps: _FakeDeps) -> None:
        self.deps = deps
        self.tool_call_id: str | None = None
        self.retry: int = 0


class _FakeRunControlManager:
    def is_run_stop_requested(self, run_id: str) -> bool:
        return False

    def is_subagent_stop_requested(self, *, run_id: str, instance_id: str) -> bool:
        return False

    def raise_if_cancelled(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
    ) -> None:
        return None


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
    meta = cast(JsonObject, result["meta"])
    assert result["ok"] is True
    assert result["tool"] == "read"
    assert result["data"] == "hello"
    assert result["error"] is None
    assert meta["approval_required"] is False


def test_execute_tool_returns_denied_error_when_approval_rejected() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("deny", "not safe")),
        policy=_FakePolicy(needs_approval=True),
    )
    ctx = _FakeCtx(deps)
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=lambda: "should_not_run",
        )
    )
    error = cast(JsonObject, result["error"])
    meta = cast(JsonObject, result["meta"])
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


def test_execute_tool_returns_timeout_error_when_approval_times_out() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(timeout=True),
        policy=_FakePolicy(needs_approval=True, timeout_seconds=0.01),
    )
    ctx = _FakeCtx(deps)
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="shell",
            args_summary={"command": "echo hi"},
            action=lambda: "should_not_run",
        )
    )
    error = cast(JsonObject, result["error"])
    meta = cast(JsonObject, result["meta"])
    assert result["ok"] is False
    assert error["type"] == "approval_timeout"
    assert meta["approval_status"] == "timeout"


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
    assert result["ok"] is True
    assert manager.last_open is not None
    assert manager.last_open["tool_call_id"] == "call-model-123"
