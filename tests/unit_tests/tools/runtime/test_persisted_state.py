# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.agents.execution.tool_result_state import ToolResultStateService
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.runtime.persisted_state import (
    ToolApprovalStatus,
    ToolCallBatchStatus,
    ToolExecutionStatus,
    load_or_recover_tool_call_state,
    load_tool_call_batch_state,
    load_tool_call_batch_state_async,
    load_tool_call_state,
    merge_tool_call_state,
    recover_tool_call_batches_from_event_log,
    recover_tool_call_state_from_event_log,
    update_tool_call_call_state_async,
)


def _event(
    event_type: RunEventType,
    *,
    payload: dict[str, object],
) -> RunEvent:
    return RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="time",
        event_type=event_type,
        payload_json=json.dumps(payload),
    )


def test_recover_tool_call_batch_and_result_from_event_log(tmp_path: Path) -> None:
    db_path = tmp_path / "persisted_state_recovery.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL,
            payload={
                "run_id": "run-1",
                "session_id": "session-1",
                "tool_name": "current_time",
                "tool_call_id": "call-a",
                "args": {"timezone": "UTC"},
                "batch_id": "batch-1",
                "batch_index": 0,
                "batch_size": 2,
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL,
            payload={
                "run_id": "run-1",
                "session_id": "session-1",
                "tool_name": "current_time",
                "tool_call_id": "call-b",
                "args": {"timezone": "Asia/Shanghai"},
                "batch_id": "batch-1",
                "batch_index": 1,
                "batch_size": 2,
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL_BATCH_SEALED,
            payload={
                "run_id": "run-1",
                "session_id": "session-1",
                "batch_id": "batch-1",
                "tool_calls": [
                    {
                        "tool_call_id": "call-a",
                        "tool_name": "current_time",
                        "args": {"timezone": "UTC"},
                        "index": 0,
                    },
                    {
                        "tool_call_id": "call-b",
                        "tool_name": "current_time",
                        "args": {"timezone": "Asia/Shanghai"},
                        "index": 1,
                    },
                ],
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )
    result_event_id = event_log.emit_run_event(
        _event(
            RunEventType.TOOL_RESULT,
            payload={
                "tool_name": "current_time",
                "tool_call_id": "call-a",
                "result": {"time": "2026-03-07T10:00:00Z"},
                "error": False,
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )

    batches = recover_tool_call_batches_from_event_log(
        event_log=event_log,
        shared_store=shared_store,
        trace_id="trace-1",
        task_id="task-1",
    )
    recovered_call = recover_tool_call_state_from_event_log(
        event_log=event_log,
        shared_store=shared_store,
        trace_id="trace-1",
        task_id="task-1",
        tool_call_id="call-a",
    )

    assert len(batches) == 1
    assert batches[0].status == ToolCallBatchStatus.SEALED
    assert [item.tool_call_id for item in batches[0].items] == ["call-a", "call-b"]
    assert json.loads(batches[0].items[0].args_preview) == {"timezone": "UTC"}
    assert json.loads(batches[0].items[1].args_preview) == {"timezone": "Asia/Shanghai"}
    assert recovered_call is not None
    assert recovered_call.execution_status == ToolExecutionStatus.COMPLETED
    assert recovered_call.batch_id == "batch-1"
    assert recovered_call.batch_index == 0
    assert recovered_call.batch_size == 2
    assert recovered_call.result_event_id == result_event_id
    assert recovered_call.result_envelope == {"time": "2026-03-07T10:00:00Z"}
    assert ToolResultStateService().visible_tool_result_from_state(
        state=recovered_call,
        expected_tool_name="current_time",
        to_json_compatible=lambda value: cast(JsonValue, value),
    ) == {"time": "2026-03-07T10:00:00Z"}


def test_recover_open_tool_call_batch_preserves_json_args_preview(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_open_batch_recovery.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL,
            payload={
                "run_id": "run-1",
                "session_id": "session-1",
                "tool_name": "current_time",
                "tool_call_id": "call-open",
                "args": {"timezone": "UTC"},
                "batch_id": "batch-open",
                "batch_index": 0,
                "batch_size": 2,
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )

    batches = recover_tool_call_batches_from_event_log(
        event_log=event_log,
        shared_store=shared_store,
        trace_id="trace-1",
        task_id="task-1",
    )
    recovered_call = recover_tool_call_state_from_event_log(
        event_log=event_log,
        shared_store=shared_store,
        trace_id="trace-1",
        task_id="task-1",
        tool_call_id="call-open",
    )

    assert len(batches) == 1
    assert batches[0].status == ToolCallBatchStatus.OPEN
    assert json.loads(batches[0].items[0].args_preview) == {"timezone": "UTC"}
    assert recovered_call is not None
    assert json.loads(recovered_call.args_preview) == {"timezone": "UTC"}


def test_tool_call_state_parallel_merges_do_not_corrupt_state(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "persisted_state_parallel.db")

    def _merge(i: int) -> None:
        merge_tool_call_state(
            shared_store=shared_store,
            task_id="task-1",
            tool_call_id=f"call-{i}",
            tool_name="current_time",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="time",
            args_preview='{"timezone": "UTC"}',
            execution_status=ToolExecutionStatus.READY,
            batch_id=f"batch-{i // 5}",
            batch_index=i % 5,
            batch_size=5,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_merge, i) for i in range(100)]
        for future in futures:
            future.result()

    snapshot = shared_store.snapshot(
        ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1")
    )
    tool_call_keys = [key for key, _value in snapshot if key.startswith("tool_call_")]
    assert len(tool_call_keys) == 100
    recovered = load_tool_call_state(
        shared_store=shared_store,
        task_id="task-1",
        tool_call_id="call-42",
    )
    assert recovered is not None
    assert recovered.batch_id == "batch-8"
    assert recovered.batch_index == 2


def test_load_or_recover_replays_result_event_over_stale_running_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_state_stale_running.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    merge_tool_call_state(
        shared_store=shared_store,
        task_id="task-1",
        tool_call_id="call-a",
        tool_name="current_time",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        args_preview='{"timezone":"UTC"}',
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={"resume_token": "token-1"},
    )
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL,
            payload={
                "run_id": "run-1",
                "session_id": "session-1",
                "tool_name": "current_time",
                "tool_call_id": "call-a",
                "args": {"timezone": "UTC"},
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )
    result_event_id = event_log.emit_run_event(
        _event(
            RunEventType.TOOL_RESULT,
            payload={
                "tool_name": "current_time",
                "tool_call_id": "call-a",
                "result": {
                    "ok": True,
                    "data": {"time": "2026-03-07T10:00:00Z"},
                    "meta": {"tool_result_event_published": True},
                },
                "error": False,
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )

    recovered_call = load_or_recover_tool_call_state(
        event_log=event_log,
        shared_store=shared_store,
        trace_id="trace-1",
        task_id="task-1",
        tool_call_id="call-a",
    )

    assert recovered_call is not None
    assert recovered_call.execution_status == ToolExecutionStatus.COMPLETED
    assert recovered_call.result_event_id == result_event_id
    assert recovered_call.call_state == {"resume_token": "token-1"}
    assert ToolResultStateService().visible_tool_result_from_state(
        state=recovered_call,
        expected_tool_name="current_time",
        to_json_compatible=lambda value: cast(JsonValue, value),
    ) == {
        "ok": True,
        "data": {"time": "2026-03-07T10:00:00Z"},
        "meta": {"tool_result_event_published": True},
    }


def test_parallel_load_or_recover_replays_result_events_without_state_corruption(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_state_parallel_recovery.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    call_count = 50
    for i in range(call_count):
        tool_call_id = f"call-{i}"
        merge_tool_call_state(
            shared_store=shared_store,
            task_id="task-1",
            tool_call_id=tool_call_id,
            tool_name="current_time",
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="time",
            args_preview='{"timezone":"UTC"}',
            execution_status=ToolExecutionStatus.RUNNING,
            call_state={"resume_token": f"token-{i}"},
        )
        event_log.emit_run_event(
            _event(
                RunEventType.TOOL_RESULT,
                payload={
                    "tool_name": "current_time",
                    "tool_call_id": tool_call_id,
                    "result": {
                        "ok": True,
                        "data": {"index": i},
                        "meta": {"tool_result_event_published": True},
                    },
                    "error": False,
                    "role_id": "time",
                    "instance_id": "inst-1",
                },
            )
        )

    def _recover(i: int) -> tuple[str, ToolExecutionStatus, dict[str, JsonValue]]:
        recovered = load_or_recover_tool_call_state(
            event_log=event_log,
            shared_store=shared_store,
            trace_id="trace-1",
            task_id="task-1",
            tool_call_id=f"call-{i}",
        )
        assert recovered is not None
        return recovered.tool_call_id, recovered.execution_status, recovered.call_state

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_recover, i) for i in range(call_count)]
        recovered = [future.result() for future in futures]

    assert len(recovered) == call_count
    assert {item[0] for item in recovered} == {f"call-{i}" for i in range(call_count)}
    assert all(item[1] == ToolExecutionStatus.COMPLETED for item in recovered)
    assert all(
        item[2]["resume_token"] == f"token-{index}"
        for index, item in enumerate(recovered)
    )


@pytest.mark.asyncio
async def test_invalid_tool_call_batch_state_returns_none_for_sync_and_async(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "persisted_state_invalid_batch.db")
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_batch:batch-invalid",
            value_json="{not-json",
        )
    )

    assert (
        load_tool_call_batch_state(
            shared_store=shared_store,
            task_id="task-1",
            batch_id="batch-invalid",
        )
        is None
    )
    assert (
        await load_tool_call_batch_state_async(
            shared_store=shared_store,
            task_id="task-1",
            batch_id="batch-invalid",
        )
        is None
    )


def test_load_or_recover_returns_linked_terminal_state_without_event_log(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(
        tmp_path / "persisted_state_linked_terminal.db"
    )
    current = merge_tool_call_state(
        shared_store=shared_store,
        task_id="task-1",
        tool_call_id="call-linked",
        tool_name="current_time",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "ok": True,
            "runtime_meta": {"tool_result_event_published": True},
        },
    )

    recovered = load_or_recover_tool_call_state(
        shared_store=shared_store,
        event_log=None,
        trace_id="trace-1",
        task_id="task-1",
        tool_call_id="call-linked",
    )

    assert recovered == current


def test_load_or_recover_preserves_current_state_when_no_recovery_event_exists(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_state_no_recovery.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    current = merge_tool_call_state(
        shared_store=shared_store,
        task_id="task-1",
        tool_call_id="call-ready",
        tool_name="current_time",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        execution_status=ToolExecutionStatus.READY,
        call_state={"resume_token": "ready-token"},
    )

    assert (
        load_or_recover_tool_call_state(
            shared_store=shared_store,
            event_log=None,
            trace_id="trace-1",
            task_id="task-1",
            tool_call_id="call-ready",
        )
        == current
    )
    assert (
        load_or_recover_tool_call_state(
            shared_store=shared_store,
            event_log=event_log,
            trace_id="trace-1",
            task_id="task-1",
            tool_call_id="call-ready",
        )
        == current
    )


def test_load_or_recover_creates_state_from_tool_call_event_without_current_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_state_call_only_recovery.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL,
            payload={
                "run_id": "run-1",
                "session_id": "session-1",
                "tool_name": "current_time",
                "tool_call_id": "call-new",
                "args": '{"timezone":"UTC"}',
                "batch_id": "batch-new",
                "batch_index": 3,
                "batch_size": 4,
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )

    recovered = load_or_recover_tool_call_state(
        shared_store=shared_store,
        event_log=event_log,
        trace_id="trace-1",
        task_id="task-1",
        tool_call_id="call-new",
    )

    assert recovered is not None
    assert recovered.execution_status == ToolExecutionStatus.READY
    assert recovered.batch_id == "batch-new"
    assert recovered.batch_index == 3
    assert recovered.batch_size == 4


def test_recover_tool_call_state_handles_denied_and_timed_out_approval_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_state_approval_resolution.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    for action in ("deny", "timeout"):
        tool_call_id = f"call-{action}"
        event_log.emit_run_event(
            _event(
                RunEventType.TOOL_APPROVAL_RESOLVED,
                payload={
                    "run_id": "run-1",
                    "session_id": "session-1",
                    "tool_name": "shell",
                    "tool_call_id": tool_call_id,
                    "action": action,
                    "feedback": f"{action} feedback",
                    "role_id": "time",
                    "instance_id": "inst-1",
                },
            )
        )

        recovered = recover_tool_call_state_from_event_log(
            event_log=event_log,
            shared_store=shared_store,
            trace_id="trace-1",
            task_id="task-1",
            tool_call_id=tool_call_id,
        )

        assert recovered is not None
        assert recovered.execution_status == ToolExecutionStatus.FAILED
        assert recovered.approval_status == (
            ToolApprovalStatus.DENY if action == "deny" else ToolApprovalStatus.TIMEOUT
        )


def test_recover_tool_call_batches_skips_malformed_batch_payloads(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persisted_state_malformed_batches.db"
    event_log = EventLog(db_path)
    shared_store = SharedStateRepository(db_path)
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="time",
            event_type=RunEventType.TOOL_CALL,
            payload_json="{not-json",
        )
    )
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL,
            payload={
                "batch_id": "batch-bad-call",
                "tool_call_id": "",
                "tool_name": "current_time",
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL_BATCH_SEALED,
            payload={
                "batch_id": "batch-invalid-list",
                "tool_calls": "not-a-list",
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )
    event_log.emit_run_event(
        _event(
            RunEventType.TOOL_CALL_BATCH_SEALED,
            payload={
                "batch_id": "batch-good",
                "tool_calls": [
                    "skip-me",
                    {"tool_call_id": "", "tool_name": "current_time"},
                    {"tool_call_id": "call-good", "tool_name": "current_time"},
                ],
                "role_id": "time",
                "instance_id": "inst-1",
            },
        )
    )

    batches = recover_tool_call_batches_from_event_log(
        event_log=event_log,
        shared_store=shared_store,
        trace_id="trace-1",
        task_id="task-1",
    )

    assert len(batches) == 1
    assert batches[0].batch_id == "batch-good"
    assert [item.tool_call_id for item in batches[0].items] == ["call-good"]
    assert batches[0].items[0].index == 2


@pytest.mark.asyncio
async def test_update_tool_call_call_state_async_merges_existing_call_state(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "persisted_state_async_update.db")
    merge_tool_call_state(
        shared_store=shared_store,
        task_id="task-1",
        tool_call_id="call-state",
        tool_name="spawn_subagent",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        call_state={"existing": True},
    )

    updated = await update_tool_call_call_state_async(
        shared_store=shared_store,
        task_id="task-1",
        tool_call_id="call-state",
        tool_name="spawn_subagent",
        instance_id="inst-1",
        role_id="writer",
        mutate=lambda current: {**current, "subagent_run_id": "subagent-run-1"},
    )

    assert updated.call_state == {
        "existing": True,
        "subagent_run_id": "subagent-run-1",
    }
