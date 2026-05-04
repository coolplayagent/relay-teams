# -*- coding: utf-8 -*-
"""Coverage gap tests for coordinator timeout policy and tool diet branches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.tasks.enums import (
    TaskStatus,
    TaskTimeoutAction,
)
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


def _make_envelope(**overrides: object) -> TaskEnvelope:
    base: dict[str, object] = dict(
        task_id="task_cov_1",
        session_id="sess_cov",
        trace_id="trace_cov",
        objective="Coverage test objective.",
        verification={},
    )
    base.update(overrides)
    return TaskEnvelope(**base)  # type: ignore[arg-type]


def _make_task_record(
    envelope: TaskEnvelope | None = None,
    status: TaskStatus = TaskStatus.RUNNING,
) -> MagicMock:
    record = MagicMock()
    record.envelope = envelope or _make_envelope()
    record.status = status
    record.assigned_instance_id = "inst_cov"
    return record


class TestCoordinatorHandleTimeoutPolicy:
    """Cover _handle_timeout_policy_async branches (lines 1537-1618)."""

    @pytest.mark.asyncio
    async def test_retry_with_attempts_remaining_enqueues(self) -> None:
        from relay_teams.agents.orchestration.coordinator import CoordinatorGraph

        coord = MagicMock(spec=CoordinatorGraph)
        wakeup_repo = AsyncMock()
        wakeup_repo.enqueue_async = AsyncMock()
        coord.wakeup_repo = wakeup_repo
        coord.event_bus = AsyncMock()
        coord.event_bus.emit_async = AsyncMock()

        envelope = _make_envelope(
            retry_attempt=1,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 3,
                "timeout_seconds": 30.0,
            },
            role_id="role_cov",
        )
        record = _make_task_record(envelope, TaskStatus.TIMEOUT)

        await CoordinatorGraph._handle_timeout_policy_async(coord, record)

        wakeup_repo.enqueue_async.assert_called_once()
        entry = wakeup_repo.enqueue_async.call_args[0][0]
        assert isinstance(entry, AgentWakeupEntry)
        assert entry.attempt == 2

    @pytest.mark.asyncio
    async def test_retry_no_wakeup_repo_logs_warning(self) -> None:
        from relay_teams.agents.orchestration.coordinator import CoordinatorGraph

        coord = MagicMock(spec=CoordinatorGraph)
        coord.wakeup_repo = None
        coord.event_bus = AsyncMock()

        envelope = _make_envelope(
            retry_attempt=0,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 3,
                "timeout_seconds": 30.0,
            },
        )
        record = _make_task_record(envelope, TaskStatus.TIMEOUT)

        # Should not raise
        await CoordinatorGraph._handle_timeout_policy_async(coord, record)

    @pytest.mark.asyncio
    async def test_retry_attempts_exhausted(self) -> None:
        from relay_teams.agents.orchestration.coordinator import CoordinatorGraph

        coord = MagicMock(spec=CoordinatorGraph)
        wakeup_repo = AsyncMock()
        coord.wakeup_repo = wakeup_repo
        coord.event_bus = AsyncMock()

        envelope = _make_envelope(
            retry_attempt=3,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 3,
                "timeout_seconds": 30.0,
            },
        )
        record = _make_task_record(envelope, TaskStatus.TIMEOUT)

        await CoordinatorGraph._handle_timeout_policy_async(coord, record)
        wakeup_repo.enqueue_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_gate_emits_event(self) -> None:
        from relay_teams.agents.orchestration.coordinator import CoordinatorGraph

        coord = MagicMock(spec=CoordinatorGraph)
        coord.event_bus = AsyncMock()
        coord.event_bus.emit_async = AsyncMock()

        envelope = _make_envelope(
            lifecycle={
                "on_timeout": TaskTimeoutAction.HUMAN_GATE,
                "timeout_seconds": 60.0,
            },
        )
        record = _make_task_record(envelope, TaskStatus.TIMEOUT)

        await CoordinatorGraph._handle_timeout_policy_async(coord, record)
        coord.event_bus.emit_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_fail_action_no_side_effects(self) -> None:
        from relay_teams.agents.orchestration.coordinator import CoordinatorGraph

        coord = MagicMock(spec=CoordinatorGraph)
        coord.event_bus = AsyncMock()
        wakeup_repo = AsyncMock()
        coord.wakeup_repo = wakeup_repo

        envelope = _make_envelope(
            lifecycle={
                "on_timeout": TaskTimeoutAction.FAIL,
                "timeout_seconds": 30.0,
            },
        )
        record = _make_task_record(envelope, TaskStatus.TIMEOUT)

        await CoordinatorGraph._handle_timeout_policy_async(coord, record)
        wakeup_repo.enqueue_async.assert_not_called()
        coord.event_bus.emit_async.assert_not_called()
