# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from relay_teams.agents.orchestration.coordinator import CoordinatorGraph
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.runtime.guardrails import (
    runtime_guardrail_report_from_event_payload,
)


class _AsyncRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def publish_async(self, event: RunEvent) -> int:
        self.events.append(event)
        return len(self.events)


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        role_id="gater",
        objective="Verify guarded runtime execution",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )


def _malformed_guardrail_report_event(task: TaskEnvelope) -> RunEvent:
    return RunEvent(
        session_id=task.session_id,
        run_id=task.trace_id,
        trace_id=task.trace_id,
        task_id=task.task_id,
        instance_id="inst-1",
        role_id=task.role_id,
        event_type=RunEventType.RUNTIME_GUARDRAIL_REPORT,
        payload_json='{"report": "not-a-report"}',
    )


@pytest.mark.asyncio
async def test_coordinator_regenerates_unparseable_runtime_guardrail_report(
    tmp_path: Path,
) -> None:
    task = _task()
    event_log = EventLog(tmp_path / "events.db")
    _ = event_log.emit_run_event(_malformed_guardrail_report_event(task))
    coordinator = CoordinatorGraph.model_construct(
        shared_store=SharedStateRepository(tmp_path / "state.db"),
        event_bus=event_log,
        run_event_hub=None,
    )

    await coordinator._ensure_runtime_guardrail_report_async(
        root_record=TaskRecord(envelope=task, assigned_instance_id="inst-1")
    )

    report_events = tuple(
        event
        for event in event_log.list_by_trace(task.trace_id)
        if event.get("event_type") == RunEventType.RUNTIME_GUARDRAIL_REPORT.value
    )
    parsed = runtime_guardrail_report_from_event_payload(
        report_events[-1].get("payload_json")
    )
    assert len(report_events) == 2
    assert parsed is not None
    assert parsed.task_id == task.task_id


@pytest.mark.asyncio
async def test_coordinator_keeps_parseable_runtime_guardrail_report(
    tmp_path: Path,
) -> None:
    task = _task()
    event_log = EventLog(tmp_path / "events.db")
    coordinator = CoordinatorGraph.model_construct(
        shared_store=SharedStateRepository(tmp_path / "state.db"),
        event_bus=event_log,
        run_event_hub=None,
    )

    await coordinator._ensure_runtime_guardrail_report_async(
        root_record=TaskRecord(envelope=task, assigned_instance_id="inst-1")
    )
    await coordinator._ensure_runtime_guardrail_report_async(
        root_record=TaskRecord(envelope=task, assigned_instance_id="inst-1")
    )

    report_events = tuple(
        event
        for event in event_log.list_by_trace(task.trace_id)
        if event.get("event_type") == RunEventType.RUNTIME_GUARDRAIL_REPORT.value
    )
    assert len(report_events) == 1


@pytest.mark.asyncio
async def test_coordinator_publishes_runtime_guardrail_report_to_hub(
    tmp_path: Path,
) -> None:
    task = _task()
    hub = _AsyncRunEventHub()
    coordinator = CoordinatorGraph.model_construct(
        shared_store=SharedStateRepository(tmp_path / "state.db"),
        event_bus=EventLog(tmp_path / "events.db"),
        run_event_hub=hub,
    )

    await coordinator._ensure_runtime_guardrail_report_async(
        root_record=TaskRecord(envelope=task, assigned_instance_id="inst-1")
    )

    assert len(hub.events) == 1
    assert hub.events[0].event_type == RunEventType.RUNTIME_GUARDRAIL_REPORT


@pytest.mark.asyncio
async def test_task_execution_service_publishes_runtime_guardrail_report_to_event_log(
    tmp_path: Path,
) -> None:
    task = _task()
    event_log = EventLog(tmp_path / "events.db")
    service = TaskExecutionService.model_construct(
        task_repo=MagicMock(),
        agent_repo=MagicMock(),
        run_runtime_repo=MagicMock(),
        shared_store=SharedStateRepository(tmp_path / "state.db"),
        event_bus=event_log,
        run_event_hub=None,
    )

    await service._control_harness().publish_guardrail_report(
        task=task,
        instance_id="inst-1",
        role_id="gater",
    )

    events = event_log.list_by_trace(task.trace_id)
    assert len(events) == 1
    assert events[0].get("event_type") == RunEventType.RUNTIME_GUARDRAIL_REPORT.value


@pytest.mark.asyncio
async def test_task_execution_service_publishes_runtime_guardrail_report_to_hub(
    tmp_path: Path,
) -> None:
    task = _task()
    hub = _AsyncRunEventHub()
    service = TaskExecutionService.model_construct(
        task_repo=MagicMock(),
        agent_repo=MagicMock(),
        run_runtime_repo=MagicMock(),
        shared_store=SharedStateRepository(tmp_path / "state.db"),
        event_bus=EventLog(tmp_path / "events.db"),
        run_event_hub=hub,
    )

    await service._control_harness().publish_guardrail_report(
        task=task,
        instance_id="inst-1",
        role_id="gater",
    )

    assert len(hub.events) == 1
    assert hub.events[0].event_type == RunEventType.RUNTIME_GUARDRAIL_REPORT
