# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_models import RunEvent


class RunStateStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatePhase(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    AWAITING_TOOL_APPROVAL = "awaiting_tool_approval"
    AWAITING_SUBAGENT_FOLLOWUP = "awaiting_subagent_followup"
    TERMINAL = "terminal"


class PendingToolApprovalState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str = Field(min_length=1)
    tool_name: str = ""
    args_preview: str = ""
    role_id: str = ""
    instance_id: str = ""
    requested_at: str = ""
    status: str = "requested"
    feedback: str = ""


class PausedSubagentState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = ""
    role_id: str = ""
    task_id: str = ""
    reason: str = ""


class RunStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    status: RunStateStatus = RunStateStatus.QUEUED
    phase: RunStatePhase = RunStatePhase.IDLE
    recoverable: bool = True
    last_event_id: int = Field(default=0, ge=0)
    checkpoint_event_id: int = Field(default=0, ge=0)
    pending_tool_approvals: tuple[PendingToolApprovalState, ...] = ()
    paused_subagent: PausedSubagentState | None = None
    updated_at: datetime


class RunSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    checkpoint_event_id: int = Field(ge=1)
    state: RunStateRecord
    created_at: datetime


_TERMINAL_STATES = {
    RunStateStatus.COMPLETED,
    RunStateStatus.FAILED,
}
_STOPPED_STATE = RunStateStatus.STOPPED
_CHECKPOINT_EVENT_TYPES = {
    RunEventType.RUN_STARTED,
    RunEventType.RUN_RESUMED,
    RunEventType.MODEL_STEP_STARTED,
    RunEventType.TOOL_APPROVAL_REQUESTED,
    RunEventType.TOOL_APPROVAL_RESOLVED,
    RunEventType.TOOL_RESULT,
    RunEventType.SUBAGENT_STOPPED,
    RunEventType.SUBAGENT_RESUMED,
    RunEventType.RUN_STOPPED,
    RunEventType.RUN_COMPLETED,
    RunEventType.RUN_FAILED,
}
_STOPPED_FOLLOWUP_EVENT_TYPES = {
    RunEventType.RUN_RESUMED,
    RunEventType.TOOL_APPROVAL_RESOLVED,
    RunEventType.SUBAGENT_RESUMED,
    RunEventType.RUN_STOPPED,
    RunEventType.RUN_COMPLETED,
    RunEventType.RUN_FAILED,
}


def initialize_run_state(event: RunEvent, event_id: int) -> RunStateRecord:
    return RunStateRecord(
        run_id=event.run_id,
        session_id=event.session_id,
        status=RunStateStatus.QUEUED,
        phase=RunStatePhase.IDLE,
        recoverable=True,
        last_event_id=max(0, event_id),
        checkpoint_event_id=max(0, event_id),
        pending_tool_approvals=(),
        paused_subagent=None,
        updated_at=event.occurred_at,
    )


def apply_run_event_to_state(
    previous: RunStateRecord | None,
    *,
    event: RunEvent,
    event_id: int,
) -> RunStateRecord:
    event_type = event.event_type
    payload = _parse_payload(event.payload_json)
    state = (
        previous.model_copy(deep=True)
        if previous is not None
        else initialize_run_state(event, event_id)
    )

    if _is_frozen_terminal(state):
        return state

    if (
        state.status == _STOPPED_STATE
        and event_type not in _STOPPED_FOLLOWUP_EVENT_TYPES
    ):
        return state

    checkpoint_event_id = state.checkpoint_event_id
    if previous is None or event_type in _CHECKPOINT_EVENT_TYPES:
        checkpoint_event_id = max(0, event_id)
    state = state.model_copy(
        update={
            "last_event_id": max(0, event_id),
            "checkpoint_event_id": checkpoint_event_id,
            "updated_at": event.occurred_at,
        }
    )

    approval_map = _approval_map(state.pending_tool_approvals)
    paused_subagent = state.paused_subagent
    status = state.status
    phase = state.phase
    recoverable = state.recoverable

    if event_type in {RunEventType.RUN_STARTED, RunEventType.RUN_RESUMED}:
        status = RunStateStatus.RUNNING
        phase = RunStatePhase.STREAMING
        recoverable = True
    elif event_type in {
        RunEventType.MODEL_STEP_STARTED,
        RunEventType.TEXT_DELTA,
        RunEventType.THINKING_STARTED,
        RunEventType.THINKING_DELTA,
        RunEventType.THINKING_FINISHED,
        RunEventType.MODEL_STEP_FINISHED,
    }:
        if status not in _TERMINAL_STATES and status != _STOPPED_STATE:
            status = RunStateStatus.RUNNING
            phase = RunStatePhase.STREAMING
    elif event_type == RunEventType.TOOL_APPROVAL_REQUESTED:
        tool_call_id = _payload_str(payload, "tool_call_id")
        if tool_call_id:
            approval_map[tool_call_id] = PendingToolApprovalState(
                tool_call_id=tool_call_id,
                tool_name=_payload_str(payload, "tool_name"),
                args_preview=_payload_str(payload, "args_preview"),
                role_id=_payload_str(payload, "role_id") or (event.role_id or ""),
                instance_id=(
                    _payload_str(payload, "instance_id") or (event.instance_id or "")
                ),
                requested_at=event.occurred_at.isoformat(),
                status="requested",
                feedback="",
            )
        status = RunStateStatus.PAUSED
        phase = RunStatePhase.AWAITING_TOOL_APPROVAL
    elif event_type in {RunEventType.TOOL_APPROVAL_RESOLVED, RunEventType.TOOL_RESULT}:
        tool_call_id = _payload_str(payload, "tool_call_id")
        if tool_call_id:
            _ = approval_map.pop(tool_call_id, None)
    elif event_type == RunEventType.SUBAGENT_STOPPED:
        paused_subagent = PausedSubagentState(
            instance_id=_payload_str(payload, "instance_id")
            or (event.instance_id or ""),
            role_id=_payload_str(payload, "role_id") or (event.role_id or ""),
            task_id=_payload_str(payload, "task_id"),
            reason=_payload_str(payload, "reason") or "stopped_by_user",
        )
        status = RunStateStatus.PAUSED
        phase = RunStatePhase.AWAITING_SUBAGENT_FOLLOWUP
    elif event_type == RunEventType.SUBAGENT_RESUMED:
        paused_subagent = None
    elif event_type == RunEventType.RUN_STOPPED:
        status = RunStateStatus.STOPPED
        phase = RunStatePhase.TERMINAL
        recoverable = True
    elif event_type == RunEventType.RUN_COMPLETED:
        payload_status = _payload_str(payload, "status").lower()
        status = (
            RunStateStatus.FAILED
            if payload_status == RunStateStatus.FAILED.value
            else RunStateStatus.COMPLETED
        )
        phase = RunStatePhase.TERMINAL
        recoverable = False
        approval_map = {}
        paused_subagent = None
    elif event_type == RunEventType.RUN_FAILED:
        status = RunStateStatus.FAILED
        phase = RunStatePhase.TERMINAL
        recoverable = False
        approval_map = {}
        paused_subagent = None

    if status not in {
        RunStateStatus.COMPLETED,
        RunStateStatus.FAILED,
        RunStateStatus.STOPPED,
    }:
        if approval_map:
            status = RunStateStatus.PAUSED
            phase = RunStatePhase.AWAITING_TOOL_APPROVAL
        elif paused_subagent is not None:
            status = RunStateStatus.PAUSED
            phase = RunStatePhase.AWAITING_SUBAGENT_FOLLOWUP
        elif status == RunStateStatus.QUEUED:
            phase = RunStatePhase.IDLE
        else:
            status = RunStateStatus.RUNNING
            phase = RunStatePhase.STREAMING

    return state.model_copy(
        update={
            "status": status,
            "phase": phase,
            "recoverable": recoverable,
            "pending_tool_approvals": tuple(approval_map.values()),
            "paused_subagent": paused_subagent,
        }
    )


def _is_frozen_terminal(state: RunStateRecord) -> bool:
    return state.status in _TERMINAL_STATES and state.phase == RunStatePhase.TERMINAL


def _approval_map(
    approvals: tuple[PendingToolApprovalState, ...],
) -> dict[str, PendingToolApprovalState]:
    result: dict[str, PendingToolApprovalState] = {}
    for item in approvals:
        result[item.tool_call_id] = item
    return result


def _parse_payload(payload_json: str) -> dict[str, object]:
    if not payload_json:
        return {}
    try:
        decoded = json.loads(payload_json)
    except Exception:
        return {}
    if not isinstance(decoded, dict):
        return {}
    result: dict[str, object] = {}
    for key, value in decoded.items():
        if isinstance(key, str):
            result[key] = value
    return result


def _payload_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""
