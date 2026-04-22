# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.sessions.runs.enums import RunEventType

from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus


class ToolApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVE = "approve"
    DENY = "deny"
    TIMEOUT = "timeout"


class ToolApprovalMode(str, Enum):
    UNKNOWN = "unknown"
    YOLO = "yolo"
    POLICY_EXEMPT = "policy_exempt"
    APPROVAL_FLOW = "approval_flow"


class ToolExecutionStatus(str, Enum):
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PersistedToolCallState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    run_id: str = ""
    session_id: str = ""
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    args_preview: str = ""
    run_yolo: bool = False
    approval_mode: ToolApprovalMode = ToolApprovalMode.UNKNOWN
    approval_status: ToolApprovalStatus = ToolApprovalStatus.PENDING
    approval_feedback: str = ""
    execution_status: ToolExecutionStatus = ToolExecutionStatus.WAITING_APPROVAL
    result_envelope: dict[str, JsonValue] | None = None
    call_state: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )


def load_tool_call_state(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    tool_call_id: str,
) -> PersistedToolCallState | None:
    raw = shared_store.get_state(_task_scope(task_id), _state_key(tool_call_id))
    if raw is None:
        return None
    try:
        return PersistedToolCallState.model_validate_json(raw)
    except Exception:
        return None


def merge_tool_call_state(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    tool_call_id: str,
    tool_name: str,
    run_id: str | None = None,
    session_id: str | None = None,
    instance_id: str,
    role_id: str,
    args_preview: str | None = None,
    run_yolo: bool | None = None,
    approval_mode: ToolApprovalMode | None = None,
    approval_status: ToolApprovalStatus | None = None,
    approval_feedback: str | None = None,
    execution_status: ToolExecutionStatus | None = None,
    result_envelope: dict[str, JsonValue] | None = None,
    call_state: dict[str, JsonValue] | None = None,
) -> PersistedToolCallState:
    current = load_tool_call_state(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
    )
    now = datetime.now(tz=timezone.utc).isoformat()
    if current is None:
        current = PersistedToolCallState(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=run_id or "",
            session_id=session_id or "",
            instance_id=instance_id,
            role_id=role_id,
            args_preview=args_preview or "",
            run_yolo=False if run_yolo is None else run_yolo,
            approval_mode=(
                ToolApprovalMode.UNKNOWN if approval_mode is None else approval_mode
            ),
            updated_at=now,
        )
    update: dict[str, object] = {
        "tool_name": tool_name,
        "instance_id": instance_id,
        "role_id": role_id,
        "updated_at": now,
    }
    if run_id is not None:
        update["run_id"] = run_id
    if session_id is not None:
        update["session_id"] = session_id
    if args_preview is not None:
        update["args_preview"] = args_preview
    if run_yolo is not None:
        update["run_yolo"] = run_yolo
    if approval_mode is not None:
        update["approval_mode"] = approval_mode
    if approval_status is not None:
        update["approval_status"] = approval_status
    if approval_feedback is not None:
        update["approval_feedback"] = approval_feedback
    if execution_status is not None:
        update["execution_status"] = execution_status
    if result_envelope is not None:
        update["result_envelope"] = result_envelope
    if call_state is not None:
        update["call_state"] = call_state
    next_state = current.model_copy(update=update)
    shared_store.manage_state(
        StateMutation(
            scope=_task_scope(task_id),
            key=_state_key(tool_call_id),
            value_json=next_state.model_dump_json(),
        )
    )
    return next_state


def load_or_recover_tool_call_state(
    *,
    shared_store: SharedStateRepository,
    event_log: EventLog,
    trace_id: str,
    task_id: str,
    tool_call_id: str,
    task_repo: TaskRepository | None = None,
) -> PersistedToolCallState | None:
    current = load_tool_call_state(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
    )
    if current is not None:
        return current
    return recover_tool_call_state_from_event_log(
        event_log=event_log,
        shared_store=shared_store,
        trace_id=trace_id,
        task_id=task_id,
        tool_call_id=tool_call_id,
        task_repo=task_repo,
    )


def update_tool_call_call_state(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    tool_call_id: str,
    tool_name: str,
    instance_id: str,
    role_id: str,
    mutate: Callable[[dict[str, JsonValue]], dict[str, JsonValue]],
) -> PersistedToolCallState:
    current = load_tool_call_state(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
    )
    base_state = dict(current.call_state) if current is not None else {}
    next_call_state = mutate(base_state)
    return merge_tool_call_state(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        run_id=None,
        session_id=None,
        instance_id=instance_id,
        role_id=role_id,
        call_state=next_call_state,
    )


def recover_tool_call_state_from_event_log(
    *,
    event_log: EventLog,
    shared_store: SharedStateRepository,
    trace_id: str,
    task_id: str,
    tool_call_id: str,
    task_repo: TaskRepository | None = None,
) -> PersistedToolCallState | None:
    recovered = load_tool_call_state(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
    )
    if recovered is not None:
        return recovered

    state: PersistedToolCallState | None = None
    tool_args: dict[str, JsonValue] = {}
    for row in event_log.list_by_trace(trace_id):
        if str(row.get("task_id") or "") != task_id:
            continue
        payload = _parse_payload(row.get("payload_json"))
        if str(payload.get("tool_call_id") or "") != tool_call_id:
            continue
        event_type = str(row.get("event_type") or "")
        if event_type == RunEventType.TOOL_CALL.value:
            tool_args = _parse_tool_args(payload)
        tool_name = str(payload.get("tool_name") or (state.tool_name if state else ""))
        run_id = str(payload.get("run_id") or (state.run_id if state else trace_id))
        session_id = str(
            payload.get("session_id")
            or row.get("session_id")
            or (state.session_id if state else "")
        )
        instance_id = str(
            payload.get("instance_id")
            or row.get("instance_id")
            or (state.instance_id if state else "")
        )
        role_id = str(payload.get("role_id") or (state.role_id if state else ""))
        args_preview = str(
            payload.get("args_preview")
            or payload.get("args")
            or (state.args_preview if state else "")
        )
        if not tool_name or not instance_id or not role_id:
            continue
        if state is None:
            state = PersistedToolCallState(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                run_id=run_id,
                session_id=session_id,
                instance_id=instance_id,
                role_id=role_id,
                args_preview=args_preview,
                run_yolo=False,
                approval_mode=ToolApprovalMode.UNKNOWN,
                approval_status=ToolApprovalStatus.NOT_REQUIRED,
                execution_status=ToolExecutionStatus.READY,
            )
        else:
            state = state.model_copy(
                update={
                    "tool_name": tool_name,
                    "run_id": run_id,
                    "session_id": session_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "args_preview": args_preview,
                }
            )

        if event_type == RunEventType.TOOL_APPROVAL_REQUESTED.value:
            state = state.model_copy(
                update={
                    "approval_mode": ToolApprovalMode.APPROVAL_FLOW,
                    "approval_status": ToolApprovalStatus.PENDING,
                    "execution_status": ToolExecutionStatus.WAITING_APPROVAL,
                }
            )
        elif event_type == RunEventType.TOOL_APPROVAL_RESOLVED.value:
            action = str(payload.get("action") or "").strip().lower()
            if action in {"approve", "approve_once", "approve_exact", "approve_prefix"}:
                state = state.model_copy(
                    update={
                        "approval_status": ToolApprovalStatus.APPROVE,
                        "approval_feedback": str(payload.get("feedback") or ""),
                        "approval_mode": ToolApprovalMode.APPROVAL_FLOW,
                        "execution_status": ToolExecutionStatus.READY,
                    }
                )
            elif action == "deny":
                state = state.model_copy(
                    update={
                        "approval_status": ToolApprovalStatus.DENY,
                        "approval_feedback": str(payload.get("feedback") or ""),
                        "approval_mode": ToolApprovalMode.APPROVAL_FLOW,
                        "execution_status": ToolExecutionStatus.FAILED,
                    }
                )
            elif action == "timeout":
                state = state.model_copy(
                    update={
                        "approval_status": ToolApprovalStatus.TIMEOUT,
                        "approval_mode": ToolApprovalMode.APPROVAL_FLOW,
                        "execution_status": ToolExecutionStatus.FAILED,
                    }
                )
        elif event_type == RunEventType.TOOL_RESULT.value:
            result = payload.get("result")
            if isinstance(result, dict):
                meta = result.get("meta")
                approval_status = None
                approval_mode = None
                run_yolo = None
                if isinstance(meta, dict):
                    approval_text = (
                        str(meta.get("approval_status") or "").strip().lower()
                    )
                    if approval_text == ToolApprovalStatus.APPROVE.value:
                        approval_status = ToolApprovalStatus.APPROVE
                    elif approval_text == ToolApprovalStatus.DENY.value:
                        approval_status = ToolApprovalStatus.DENY
                    elif approval_text == ToolApprovalStatus.TIMEOUT.value:
                        approval_status = ToolApprovalStatus.TIMEOUT
                    elif approval_text == ToolApprovalStatus.NOT_REQUIRED.value:
                        approval_status = ToolApprovalStatus.NOT_REQUIRED
                    approval_mode = _parse_approval_mode(meta.get("approval_mode"))
                    if isinstance(meta.get("run_yolo"), bool):
                        run_yolo = bool(meta["run_yolo"])
                state = state.model_copy(
                    update={
                        "run_yolo": state.run_yolo if run_yolo is None else run_yolo,
                        "approval_mode": (
                            state.approval_mode
                            if approval_mode is None
                            else approval_mode
                        ),
                        "approval_status": approval_status or state.approval_status,
                        "execution_status": ToolExecutionStatus.COMPLETED,
                        "result_envelope": result,
                    }
                )

    if state is None:
        return None
    recovered_call_state = dict(state.call_state)
    if not recovered_call_state:
        recovered_call_state = _recover_call_state(
            tool_name=state.tool_name,
            trace_id=trace_id,
            task_id=task_id,
            tool_args=tool_args,
            shared_store=shared_store,
            task_repo=task_repo,
        )
    return merge_tool_call_state(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_id=tool_call_id,
        tool_name=state.tool_name,
        run_id=state.run_id,
        session_id=state.session_id,
        instance_id=state.instance_id,
        role_id=state.role_id,
        args_preview=state.args_preview,
        run_yolo=state.run_yolo,
        approval_mode=state.approval_mode,
        approval_status=state.approval_status,
        approval_feedback=state.approval_feedback,
        execution_status=state.execution_status,
        result_envelope=state.result_envelope,
        call_state=recovered_call_state,
    )


def _task_scope(task_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.TASK, scope_id=task_id)


def _state_key(tool_call_id: str) -> str:
    return f"tool_call_state:{tool_call_id}"


def _parse_payload(raw_payload: object) -> dict[str, JsonValue]:
    if not isinstance(raw_payload, str) or not raw_payload:
        return {}
    try:
        decoded = json.loads(raw_payload)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_tool_args(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw_args = payload.get("args")
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            decoded = json.loads(raw_args)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _parse_approval_mode(value: object) -> ToolApprovalMode | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    for mode in ToolApprovalMode:
        if normalized == mode.value:
            return mode
    return None


def _recover_call_state(
    *,
    tool_name: str,
    trace_id: str,
    task_id: str,
    tool_args: dict[str, JsonValue],
    shared_store: SharedStateRepository,
    task_repo: TaskRepository | None,
) -> dict[str, JsonValue]:
    if tool_name != "orch_dispatch_task" or task_repo is None:
        return {}
    return _recover_dispatch_task_call_state(
        trace_id=trace_id,
        tool_args=tool_args,
        task_repo=task_repo,
    )


def _recover_dispatch_task_call_state(
    *,
    trace_id: str,
    tool_args: dict[str, JsonValue],
    task_repo: TaskRepository,
) -> dict[str, JsonValue]:
    dispatched_task_id = str(tool_args.get("task_id") or "").strip()
    if not dispatched_task_id:
        return {}
    record = task_repo.get(dispatched_task_id)
    if record.envelope.trace_id != trace_id:
        return {}
    prompt = str(tool_args.get("prompt") or "")
    return {
        "kind": "orch_dispatch_task",
        "task_id": dispatched_task_id,
        "prompt": prompt,
        "role_id": str(tool_args.get("role_id") or record.envelope.role_id or ""),
        "instance_id": str(record.assigned_instance_id or ""),
        "execution_started": record.status
        not in {TaskStatus.CREATED, TaskStatus.ASSIGNED},
    }
