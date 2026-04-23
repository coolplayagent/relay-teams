# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pydantic import BaseModel, JsonValue

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import cast

import pytest

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.notifications import NotificationService, default_notification_config
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookEventName
from relay_teams.sessions.runs.event_stream import RunEventHub

from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.execution import (
    _resolve_runtime_active_local_tools,
    execute_tool,
    execute_tool_call,
)
from relay_teams.tools.runtime.models import (
    ToolExecutionError,
    ToolResultProjection,
)
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.runtime.persisted_state import load_tool_call_state
from relay_teams.tools.runtime.persisted_state import ToolApprovalMode


class _TaskDraftPayload(BaseModel):
    objective: str
    title: str | None = None


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeInjectionRecord:
    def __init__(self, *, source: InjectionSource, content: str) -> None:
        self.source = source
        self.content = content


class _FakeInjectionManager:
    def __init__(self) -> None:
        self.records: list[_FakeInjectionRecord] = []

    def is_active(self, run_id: str) -> bool:
        _ = run_id
        return True

    def enqueue(
        self,
        run_id: str,
        recipient_instance_id: str,
        *,
        source: InjectionSource,
        content: str,
    ) -> _FakeInjectionRecord:
        _ = (run_id, recipient_instance_id)
        record = _FakeInjectionRecord(source=source, content=content)
        self.records.append(record)
        return record


class _FakeAgentInstanceRecord:
    def __init__(
        self,
        *,
        run_id: str,
        trace_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        status: InstanceStatus,
    ) -> None:
        _ = (run_id, trace_id, session_id, role_id, workspace_id, conversation_id)
        self.instance_id = instance_id
        self.status = status
        self.runtime_system_prompt = ""
        self.runtime_tools_json = ""
        self.runtime_active_tools_json = ""


class _FakeAgentRepo:
    def __init__(self) -> None:
        self._instances: dict[str, _FakeAgentInstanceRecord] = {}

    def upsert_instance(
        self,
        *,
        run_id: str,
        trace_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        status: InstanceStatus,
    ) -> None:
        self._instances[instance_id] = _FakeAgentInstanceRecord(
            run_id=run_id,
            trace_id=trace_id,
            session_id=session_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            status=status,
        )

    def get_instance(self, instance_id: str) -> _FakeAgentInstanceRecord:
        try:
            return self._instances[instance_id]
        except KeyError as exc:
            raise KeyError(instance_id) from exc

    def update_runtime_snapshot(
        self,
        instance_id: str,
        *,
        runtime_system_prompt: str,
        runtime_tools_json: str,
        runtime_active_tools_json: str,
    ) -> None:
        record = self.get_instance(instance_id)
        record.runtime_system_prompt = runtime_system_prompt
        record.runtime_tools_json = runtime_tools_json
        record.runtime_active_tools_json = runtime_active_tools_json


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
        self.workspace_id = "workspace-1"
        self.conversation_id = "conversation-1"
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
        self.hook_service: object | None = None
        self.hook_runtime_env: dict[str, str] = {}
        self.injection_manager = _FakeInjectionManager()
        self.agent_repo: AgentInstanceRepository | _FakeAgentRepo | None = (
            _FakeAgentRepo()
        )
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
        self.agent_repo.upsert_instance(
            run_id=self.run_id,
            trace_id=self.trace_id,
            session_id=self.session_id,
            instance_id=self.instance_id,
            role_id=self.role_id,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            status=InstanceStatus.IDLE,
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


@pytest.mark.timeout(5)
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


def test_execute_tool_honors_persisted_approval_when_timeout_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(timeout=True),
        policy=_FakePolicy(needs_approval=True, timeout_seconds=0.01),
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-model-race"
    original_resolve = deps.approval_ticket_repo.resolve

    def resolve_with_approved_race(
        *,
        tool_call_id: str,
        status: ApprovalTicketStatus,
        feedback: str = "",
        expected_status: ApprovalTicketStatus | None = None,
    ):
        if (
            tool_call_id == "call-model-race"
            and status == ApprovalTicketStatus.TIMED_OUT
            and expected_status == ApprovalTicketStatus.REQUESTED
        ):
            _ = original_resolve(
                tool_call_id=tool_call_id,
                status=ApprovalTicketStatus.APPROVED,
                feedback="approved elsewhere",
            )
            raise ApprovalTicketStatusConflictError(
                tool_call_id=tool_call_id,
                expected_status=ApprovalTicketStatus.REQUESTED,
                actual_status=ApprovalTicketStatus.APPROVED,
            )
        return original_resolve(
            tool_call_id=tool_call_id,
            status=status,
            feedback=feedback,
            expected_status=expected_status,
        )

    monkeypatch.setattr(
        deps.approval_ticket_repo, "resolve", resolve_with_approved_race
    )

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="shell",
            args_summary={"command": "echo hi"},
            action=lambda: "executed",
        )
    )

    ticket = deps.approval_ticket_repo.get("call-model-race")
    assert result["ok"] is True
    assert result["data"] == "executed"
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.COMPLETED


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
            tool_name="orch_dispatch_task",
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
    assert tool_result_payloads[0]["tool_name"] == "orch_dispatch_task"
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


def test_execute_tool_blocks_deferred_local_tools_until_activation() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.agent_repo = AgentInstanceRepository(Path(mkdtemp()) / "instances.db")
    deps.agent_repo.upsert_instance(
        run_id=deps.run_id,
        trace_id=deps.trace_id,
        session_id=deps.session_id,
        instance_id=deps.instance_id,
        role_id=deps.role_id,
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.IDLE,
    )
    deps.agent_repo.update_runtime_snapshot(
        deps.instance_id,
        runtime_system_prompt="runtime prompt",
        runtime_tools_json=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="activate_tools",
                    description="Activate tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read files.",
                ),
            ),
        ).model_dump_json(),
        runtime_active_tools_json='["tool_search","activate_tools"]',
    )
    ctx = _FakeCtx(deps)
    action_calls: list[str] = []

    deferred_result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: action_calls.append("read") or "hello",
        )
    )

    deferred_error = cast(dict[str, JsonValue], deferred_result["error"])
    assert deferred_result["ok"] is False
    assert deferred_error["type"] == "validation_error"
    assert "currently deferred" in str(deferred_error["message"])
    assert action_calls == []

    deps.agent_repo.update_runtime_snapshot(
        deps.instance_id,
        runtime_system_prompt="runtime prompt",
        runtime_tools_json=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="activate_tools",
                    description="Activate tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read files.",
                ),
            ),
        ).model_dump_json(),
        runtime_active_tools_json='["tool_search","activate_tools","read"]',
    )

    activated_result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: action_calls.append("read") or "hello",
        )
    )

    assert activated_result["ok"] is True
    assert activated_result["data"] == "hello"
    assert action_calls == ["read"]


def test_execute_tool_fails_closed_when_agent_repo_contract_is_unavailable() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.agent_repo = cast(AgentInstanceRepository, cast(object, None))
    ctx = _FakeCtx(deps)
    action_calls: list[str] = []

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: action_calls.append("read") or "hello",
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    assert result["ok"] is False
    assert error["type"] == "internal_error"
    assert "agent instance repository contract" in str(error["message"])
    assert action_calls == []


def test_resolve_runtime_active_local_tools_restores_required_discovery_tools() -> None:
    assert _resolve_runtime_active_local_tools(
        authorized_local_tools=("tool_search", "activate_tools", "read"),
        runtime_active_tools_json='["read"]',
    ) == (
        "tool_search",
        "activate_tools",
        "read",
    )


def test_execute_tool_call_rehydrates_pydantic_model_lists_from_future_annotations() -> (
    None
):
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    captured_types: list[type[object]] = []

    async def _action(tasks: list[_TaskDraftPayload]) -> dict[str, JsonValue]:
        captured_types.extend(type(task) for task in tasks)
        return {
            "titles": [task.title for task in tasks],
        }

    result = asyncio.run(
        execute_tool_call(
            cast(ToolContext, cast(object, ctx)),
            tool_name="orch_create_tasks",
            args_summary={"task_count": 1},
            action=_action,
            raw_args={
                "ctx": ctx,
                "tasks": [
                    _TaskDraftPayload(
                        objective="Implement the endpoint",
                        title="Endpoint implementation",
                    )
                ],
            },
        )
    )

    assert result["ok"] is True
    assert result["data"] == {"titles": ["Endpoint implementation"]}
    assert captured_types == [_TaskDraftPayload]


def test_execute_tool_call_rehydrates_optional_pydantic_models_from_future_annotations() -> (
    None
):
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    ctx = _FakeCtx(deps)
    captured_type: list[type[object]] = []

    async def _action(task: _TaskDraftPayload | None = None) -> dict[str, JsonValue]:
        if task is None:
            return {"title": None}
        captured_type.append(type(task))
        return {"title": task.title}

    result = asyncio.run(
        execute_tool_call(
            cast(ToolContext, cast(object, ctx)),
            tool_name="create_task",
            args_summary={"has_task": True},
            action=_action,
            raw_args={
                "ctx": ctx,
                "task": _TaskDraftPayload(
                    objective="Implement the endpoint",
                    title="Endpoint implementation",
                ),
            },
        )
    )

    assert result["ok"] is True
    assert result["data"] == {"title": "Endpoint implementation"}
    assert captured_type == [_TaskDraftPayload]


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
            tool_name="orch_dispatch_task",
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


class _FakeHookService:
    def __init__(
        self,
        decision: HookDecisionType | None = None,
        *,
        reason: str = "",
        bundles: dict[HookEventName, HookDecisionBundle] | None = None,
    ) -> None:
        self.decision = decision or HookDecisionType.ALLOW
        self.reason = reason
        self.bundles = bundles or {}

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = run_event_hub
        event_name = cast(HookEventName, getattr(event_input, "event_name"))
        if event_name in self.bundles:
            return self.bundles[event_name]
        return HookDecisionBundle(decision=self.decision, reason=self.reason)


def test_execute_tool_denies_pre_tool_use_when_hook_blocks_call() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.hook_service = _FakeHookService(
        HookDecisionType.DENY,
        reason="Shell commands are blocked in this workspace.",
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-deny-1"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="shell",
            args_summary={"command": "rm -rf ."},
            action=lambda: "should_not_run",
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    assert result["ok"] is False
    assert error["type"] == "hook_denied"
    assert "blocked" in str(error["message"]).lower()


def test_execute_tool_allows_permission_request_when_hook_overrides_approval() -> None:
    manager = _FakeApprovalManager(wait_result=("approve", ""))
    deps = _FakeDeps(
        manager=manager,
        policy=_FakePolicy(needs_approval=True),
    )
    deps.hook_service = _FakeHookService(
        bundles={
            HookEventName.PRE_TOOL_USE: HookDecisionBundle(
                decision=HookDecisionType.ALLOW,
            ),
            HookEventName.PERMISSION_REQUEST: HookDecisionBundle(
                decision=HookDecisionType.ALLOW,
            ),
        }
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-allow-approval"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=lambda: "written",
        )
    )

    state = load_tool_call_state(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        tool_call_id="call-hook-allow-approval",
    )
    assert result["ok"] is True
    assert result["data"] == "written"
    assert state is not None
    assert state.approval_mode == ToolApprovalMode.POLICY_EXEMPT
    assert manager.last_open is None
    assert not any(
        event.event_type == RunEventType.TOOL_APPROVAL_REQUESTED
        for event in deps.run_event_hub.events
    )


def test_execute_tool_records_post_tool_hook_metadata() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.hook_service = _FakeHookService(
        bundles={
            HookEventName.POST_TOOL_USE: HookDecisionBundle(
                decision=HookDecisionType.CONTINUE,
                additional_context=("summarize result",),
                deferred_action="schedule_follow_up",
            ),
        }
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-post-success"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: "hello",
        )
    )

    meta = cast(dict[str, JsonValue], result["meta"])
    assert result["ok"] is True
    assert meta["hook_additional_context"] == ["summarize result"]
    assert meta["hook_deferred_action"] == "schedule_follow_up"


def test_execute_tool_enqueues_post_tool_hook_additional_context() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.hook_service = _FakeHookService(
        bundles={
            HookEventName.POST_TOOL_USE: HookDecisionBundle(
                decision=HookDecisionType.CONTINUE,
                additional_context=("summarize result", "capture side effects"),
            ),
        }
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-post-context"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: "hello",
        )
    )

    meta = cast(dict[str, JsonValue], result["meta"])
    assert result["ok"] is True
    assert meta["hook_additional_context"] == [
        "summarize result",
        "capture side effects",
    ]
    assert [record.content for record in deps.injection_manager.records] == [
        "summarize result\n\ncapture side effects"
    ]


def test_execute_tool_records_failure_hook_deferred_event_source() -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.hook_service = _FakeHookService(
        bundles={
            HookEventName.POST_TOOL_USE_FAILURE: HookDecisionBundle(
                decision=HookDecisionType.CONTINUE,
                deferred_action="recover from failure",
            ),
        }
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-hook-post-failure"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "README.md"},
            action=lambda: (_ for _ in ()).throw(ValueError("boom")),
        )
    )

    assert result["ok"] is False
    hook_events = [
        event
        for event in deps.run_event_hub.events
        if event.event_type == RunEventType.HOOK_DEFERRED
    ]
    assert len(hook_events) == 1
    payload = cast(dict[str, object], json.loads(hook_events[0].payload_json))
    assert payload["hook_event"] == HookEventName.POST_TOOL_USE_FAILURE.value
    assert [record.content for record in deps.injection_manager.records] == [
        "recover from failure"
    ]
