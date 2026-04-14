# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pydantic import JsonValue

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import cast

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.notifications import NotificationService, default_notification_config
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager

from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.tools.runtime import (
    ToolApprovalPolicy,
    ToolContext,
    ToolExecutionError,
    ToolResultProjection,
    execute_tool,
)
from relay_teams.tools.runtime.persisted_state import load_tool_call_state
from relay_teams.tools.runtime.persisted_state import ToolApprovalMode
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookEventName


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
        hook_service: object | None = None,
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
        self.injection_manager = RunInjectionManager()
        self.injection_manager.activate(self.run_id)
        self.run_control_manager = _FakeRunControlManager()
        self.tool_approval_manager = manager
        self.tool_approval_policy = policy
        self.notification_service = _build_notification_service(self.run_event_hub)
        self.hook_service = hook_service
        self.approval_ticket_repo = ApprovalTicketRepository(db_path)
        self.run_runtime_repo = RunRuntimeRepository(db_path)
        self.shared_store = SharedStateRepository(Path(mkdtemp()) / "state.db")
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


def _tool_result_payloads(deps: _FakeDeps) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(event.payload_json))
        for event in deps.run_event_hub.events
        if event.event_type == RunEventType.TOOL_RESULT
    ]


def test_execute_tool_returns_standard_envelope() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-read-1"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: "hello",
        )
    )
    state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-read-1",
    )
    runtime = deps.run_runtime_repo.get(deps.run_id)
    assert result["ok"] is True
    assert result["data"] == "hello"
    assert result["error"] is None
    assert state is not None
    assert state.result_envelope is not None
    record = cast(dict[str, JsonValue], state.result_envelope)
    assert record["tool"] == "read"
    assert cast(dict[str, JsonValue], record["visible_result"]) == result
    runtime_meta = cast(dict[str, JsonValue], record["runtime_meta"])
    assert state.run_id == deps.run_id
    assert state.session_id == deps.session_id
    assert state.run_yolo is False
    assert state.approval_mode == ToolApprovalMode.POLICY_EXEMPT
    assert runtime_meta["approval_required"] is False
    assert runtime_meta["run_yolo"] is False
    assert runtime_meta["approval_mode"] == "policy_exempt"
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.RUNNING
    assert runtime.phase == RunRuntimePhase.SUBAGENT_RUNNING
    tool_result_payloads = _tool_result_payloads(deps)
    assert len(tool_result_payloads) == 1
    assert tool_result_payloads[0]["tool_name"] == "read"
    assert tool_result_payloads[0]["tool_call_id"] == "call-read-1"
    assert tool_result_payloads[0]["error"] is False
    assert tool_result_payloads[0]["result"] == result


def test_execute_tool_skips_approval_flow_when_yolo_enabled() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=ToolApprovalPolicy(
            yolo=True,
            timeout_seconds=0.01,
        ),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-yolo"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="webfetch",
            args_summary={"url": "https://example.com/docs"},
            approval_args_summary={"host": "example.com"},
            keep_approval_ticket_reusable=True,
            action=lambda: {"stdout": "/tmp"},
        )
    )

    state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-model-yolo",
    )
    assert result["ok"] is True
    assert result["data"] == {"stdout": "/tmp"}
    assert state is not None
    assert state.result_envelope is not None
    runtime_meta = cast(
        dict[str, JsonValue],
        cast(dict[str, JsonValue], state.result_envelope)["runtime_meta"],
    )
    assert state.run_yolo is True
    assert state.approval_mode == ToolApprovalMode.YOLO
    assert runtime_meta["approval_required"] is False
    assert runtime_meta["approval_status"] == "not_required"
    assert runtime_meta["run_yolo"] is True
    assert runtime_meta["approval_mode"] == "yolo"
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
    ticket = deps.approval_ticket_repo.get("call-model-deny")
    assert result["ok"] is False
    assert error["type"] == "approval_denied"
    assert "suggested_fix" not in error
    state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-model-deny",
    )
    assert state is not None
    assert state.result_envelope is not None
    runtime_meta = cast(
        dict[str, JsonValue],
        cast(dict[str, JsonValue], state.result_envelope)["runtime_meta"],
    )
    assert state.run_yolo is False
    assert state.approval_mode == ToolApprovalMode.APPROVAL_FLOW
    assert runtime_meta["approval_required"] is True
    assert runtime_meta["approval_status"] == "deny"
    assert runtime_meta["run_yolo"] is False
    assert runtime_meta["approval_mode"] == "approval_flow"
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
    tool_result_payloads = _tool_result_payloads(deps)
    assert len(tool_result_payloads) == 1
    assert tool_result_payloads[0]["tool_name"] == "write"
    assert tool_result_payloads[0]["tool_call_id"] == "call-model-deny"
    assert tool_result_payloads[0]["error"] is True
    assert tool_result_payloads[0]["result"] == result


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
    ticket = deps.approval_ticket_repo.get("call-model-123")
    assert result["ok"] is False
    assert error["type"] == "approval_timeout"
    assert "suggested_fix" not in error
    state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-model-123",
    )
    assert state is not None
    assert state.result_envelope is not None
    runtime_meta = cast(
        dict[str, JsonValue],
        cast(dict[str, JsonValue], state.result_envelope)["runtime_meta"],
    )
    assert state.approval_mode == ToolApprovalMode.APPROVAL_FLOW
    assert runtime_meta["approval_status"] == "timeout"
    assert runtime_meta["approval_mode"] == "approval_flow"
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.TIMED_OUT


def test_execute_tool_preserves_custom_tool_error_details() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-webfetch-error"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="webfetch",
            args_summary={"url": "https://example.com"},
            action=lambda: _raise_tool_execution_error(),
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    meta = cast(dict[str, JsonValue], result["meta"])
    assert result["ok"] is False
    assert error["type"] == "source_access_denied"
    assert error["retryable"] is False
    assert cast(dict[str, JsonValue], error["details"]) == {
        "url_host": "example.com",
        "status_code": 403,
    }
    assert cast(dict[str, JsonValue], meta["error_details"]) == {
        "url_host": "example.com",
        "status_code": 403,
    }
    tool_result_payloads = _tool_result_payloads(deps)
    assert len(tool_result_payloads) == 1
    assert tool_result_payloads[0]["tool_name"] == "webfetch"
    assert tool_result_payloads[0]["tool_call_id"] == "call-webfetch-error"
    assert tool_result_payloads[0]["error"] is True
    assert tool_result_payloads[0]["result"] == result


def test_execute_tool_publishes_sanitized_dispatch_task_result_immediately() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "dispatch-call-1"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="dispatch_task",
            args_summary={"task_name": "ask_time"},
            action=lambda: {
                "task_status": {
                    "ask_time": {
                        "task_name": "ask_time",
                        "task_id": "task-1",
                        "role_id": "time",
                        "instance_id": "inst-1",
                        "status": "completed",
                        "result": "Current time is 2026-03-07 00:41:29.",
                        "error": "Task stopped by user",
                    }
                }
            },
        )
    )

    tool_result_payloads = _tool_result_payloads(deps)
    assert len(tool_result_payloads) == 1
    payload_result = cast(dict[str, object], tool_result_payloads[0]["result"])
    task_status = cast(
        dict[str, object],
        cast(dict[str, object], payload_result["data"])["task_status"],
    )["ask_time"]
    task_status_payload = cast(dict[str, object], task_status)
    assert tool_result_payloads[0]["tool_name"] == "dispatch_task"
    assert tool_result_payloads[0]["tool_call_id"] == "dispatch-call-1"
    assert tool_result_payloads[0]["error"] is False
    assert task_status_payload["status"] == "completed"
    assert task_status_payload["result"] == "Current time is 2026-03-07 00:41:29."
    assert "error" not in task_status_payload
    assert result["ok"] is True


def test_execute_tool_marks_value_error_as_non_retryable() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-validation-error-1"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "notes.txt"},
            action=lambda: (_ for _ in ()).throw(ValueError("missing path")),
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    assert result["ok"] is False
    assert error["type"] == "validation_error"
    assert error["retryable"] is False


def _raise_tool_execution_error() -> object:
    raise ToolExecutionError(
        error_type="source_access_denied",
        message="Web fetch failed for example.com with HTTP 403",
        retryable=False,
        details={"url_host": "example.com", "status_code": 403},
    )


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


def test_execute_tool_reuses_host_scoped_approval_identity_after_success() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=_FakePolicy(needs_approval=True),
    )
    first_ctx = _FakeCtx(deps)
    first_ctx.tool_call_id = "call-webfetch-1"
    first_result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, first_ctx)),
            tool_name="webfetch",
            args_summary={"url": "https://example.com/docs/start"},
            approval_args_summary={"host": "example.com"},
            keep_approval_ticket_reusable=True,
            action=lambda: "first",
        )
    )

    first_state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-webfetch-1",
    )
    first_ticket = deps.approval_ticket_repo.get("call-webfetch-1")
    assert first_result["ok"] is True
    assert first_state is not None
    assert first_state.args_preview == '{"url": "https://example.com/docs/start"}'
    assert first_ticket is not None
    assert first_ticket.status == ApprovalTicketStatus.APPROVED

    manager.last_open = None
    second_ctx = _FakeCtx(deps)
    second_ctx.tool_call_id = "call-webfetch-2"
    second_result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, second_ctx)),
            tool_name="webfetch",
            args_summary={"url": "https://example.com/docs/next"},
            approval_args_summary={"host": "example.com"},
            keep_approval_ticket_reusable=True,
            action=lambda: "second",
        )
    )

    second_state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-webfetch-2",
    )
    assert second_result["ok"] is True
    assert second_result["data"] == "second"
    assert second_state is not None
    assert second_state.args_preview == '{"url": "https://example.com/docs/next"}'
    assert manager.last_open is None
    assert (
        len(
            [
                event
                for event in deps.run_event_hub.events
                if event.event_type == RunEventType.TOOL_APPROVAL_REQUESTED
            ]
        )
        == 1
    )


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


def test_execute_tool_supports_projection_with_separate_visible_and_internal_data() -> (
    None
):
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-projection-1"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="shell",
            args_summary={"command": "pwd"},
            action=lambda: ToolResultProjection(
                visible_data={"output": "/tmp", "exit_code": 0},
                internal_data={"stdout": "/tmp\n", "stderr": "", "exit_code": 0},
            ),
        )
    )

    state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-projection-1",
    )

    assert result["ok"] is True
    assert result["data"] == {"output": "/tmp", "exit_code": 0}
    assert result["error"] is None
    meta = cast(dict[str, JsonValue], result["meta"])
    assert meta["approval_required"] is False
    assert meta["approval_status"] == "not_required"
    assert meta["approval_mode"] == "policy_exempt"
    duration_ms = cast(int, meta["duration_ms"])
    assert duration_ms >= 0
    assert state is not None
    assert state.result_envelope is not None
    assert state.approval_mode == ToolApprovalMode.POLICY_EXEMPT
    internal_data = cast(
        dict[str, JsonValue],
        cast(dict[str, JsonValue], state.result_envelope)["internal_data"],
    )
    assert internal_data["stdout"] == "/tmp\n"


def test_load_tool_call_state_tolerates_legacy_rows_without_yolo_fields() -> None:
    shared_store = SharedStateRepository(Path(mkdtemp()) / "legacy-state.db")
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-legacy"),
            key="tool_call_state:call-legacy",
            value_json=json.dumps(
                {
                    "tool_call_id": "call-legacy",
                    "tool_name": "websearch",
                    "instance_id": "inst-legacy",
                    "role_id": "spec_coder",
                    "args_preview": '{"query":"legacy"}',
                    "approval_status": "not_required",
                    "approval_feedback": "",
                    "execution_status": "completed",
                    "result_envelope": None,
                    "call_state": {},
                    "created_at": "2026-03-31T00:00:00+00:00",
                    "updated_at": "2026-03-31T00:00:00+00:00",
                }
            ),
        )
    )

    state = load_tool_call_state(
        shared_store=shared_store,
        task_id="task-legacy",
        tool_call_id="call-legacy",
    )

    assert state is not None
    assert state.run_id == ""
    assert state.session_id == ""
    assert state.run_yolo is False
    assert state.approval_mode == ToolApprovalMode.UNKNOWN


def test_execute_tool_marks_sqlite_lock_error_as_retryable() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-db-lock-1"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="dispatch_task",
            args_summary={"task_id": "task-2"},
            action=lambda: (_ for _ in ()).throw(
                sqlite3.OperationalError("database is locked")
            ),
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    assert result["ok"] is False
    assert error["type"] == "internal_error"
    assert error["retryable"] is True


class _StaticHookService:
    def __init__(self, responses: dict[HookEventName, HookDecisionBundle]) -> None:
        self._responses = responses

    async def run_tool_event(
        self,
        *,
        event_name: HookEventName,
        event: object,
    ) -> HookDecisionBundle:
        _ = event
        return self._responses.get(
            event_name,
            HookDecisionBundle(decision=HookDecisionType.ALLOW),
        )


def test_execute_tool_returns_hook_denied_error_before_action() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
        hook_service=_StaticHookService(
            {
                HookEventName.PRE_TOOL_USE: HookDecisionBundle(
                    decision=HookDecisionType.DENY,
                    reason="blocked by hook",
                )
            }
        ),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-deny"
    called = False

    def _action() -> str:
        nonlocal called
        called = True
        return "should-not-run"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=_action,
        )
    )

    assert result["ok"] is False
    assert cast(dict[str, JsonValue], result["error"])["type"] == "hook_denied"
    assert called is False


def test_execute_tool_hook_can_force_approval() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", "approved by hook"))
    deps = _FakeDeps(
        manager=manager,
        policy=_FakePolicy(needs_approval=False),
        hook_service=_StaticHookService(
            {
                HookEventName.PRE_TOOL_USE: HookDecisionBundle(
                    decision=HookDecisionType.ASK,
                    reason="review this call",
                )
            }
        ),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-ask"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: "ok",
        )
    )

    assert result["ok"] is True
    assert manager.last_open is not None


def test_execute_tool_post_hook_enqueues_system_injection() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
        hook_service=_StaticHookService(
            {
                HookEventName.POST_TOOL_USE: HookDecisionBundle(
                    decision=HookDecisionType.CONTINUE,
                    additional_context="verify the result before replying",
                )
            }
        ),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-post"
    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: "ok",
        )
    )

    queued = deps.injection_manager.drain_at_boundary(deps.run_id, deps.instance_id)
    assert result["ok"] is True
    assert len(queued) == 1
    assert queued[0].content == "verify the result before replying"
