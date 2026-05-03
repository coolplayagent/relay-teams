# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Generator

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskLifecyclePolicy,
    TaskRecord,
    VerificationPlan,
)
from relay_teams.agents.orchestration.stale_task_sweeper import StaleTaskSweeper
from relay_teams.sessions.runs.event_log import EventLog

_DEFAULT_LIFECYCLE = TaskLifecyclePolicy(
    heartbeat_interval_seconds=30.0,
    stale_silence_multiplier=3.0,
    on_timeout=TaskTimeoutAction.RETRY,
    max_retry_attempts=3,
)


def _make_task_record(
    *,
    task_id: str = "task_stale",
    heartbeat_seconds: float = 30.0,
    stale_multiplier: float = 3.0,
    on_timeout: TaskTimeoutAction = TaskTimeoutAction.RETRY,
    updated_at_ago_seconds: float = 200.0,
) -> TaskRecord:
    now = datetime.now(tz=timezone.utc)
    lifecycle = TaskLifecyclePolicy(
        heartbeat_interval_seconds=heartbeat_seconds,
        stale_silence_multiplier=stale_multiplier,
        on_timeout=on_timeout,
        max_retry_attempts=3,
    )
    envelope = TaskEnvelope(
        task_id=task_id,
        session_id="sess1",
        trace_id="trace1",
        role_id="Crafter",
        objective="Test task",
        retry_attempt=0,
        lifecycle=lifecycle,
        verification=VerificationPlan(),
    )
    return TaskRecord(
        envelope=envelope,
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now - timedelta(seconds=updated_at_ago_seconds),
    )


@pytest.fixture
def wakeup_repo() -> Generator[AgentWakeupRepository, None, None]:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_path = Path(tmpdir) / "test.db"
        yield AgentWakeupRepository(db_path)


class TestStaleTaskSweeper:
    @pytest.mark.asyncio
    async def test_sweep_detects_stale_running_task(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_task_record(updated_at_ago_seconds=200.0)
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        event_log = AsyncMock(spec=EventLog)
        event_log.emit_async = AsyncMock()

        sweeper = StaleTaskSweeper(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            event_log=event_log,
        )
        await sweeper._sweep_once_async()

        task_repo.update_status_async.assert_called()
        call_args = task_repo.update_status_async.call_args
        assert call_args[0][0] == "task_stale"
        assert call_args[0][1] == TaskStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_sweep_skips_non_stale_task(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_task_record(updated_at_ago_seconds=10.0)
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        event_log = AsyncMock(spec=EventLog)

        sweeper = StaleTaskSweeper(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            event_log=event_log,
        )
        await sweeper._sweep_once_async()

        task_repo.update_status_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_sweep_enqueues_retry_wakeup(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_task_record(
            on_timeout=TaskTimeoutAction.RETRY,
            updated_at_ago_seconds=200.0,
        )
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        event_log = AsyncMock(spec=EventLog)
        event_log.emit_async = AsyncMock()

        sweeper = StaleTaskSweeper(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            event_log=event_log,
        )
        await sweeper._sweep_once_async()

        count = await wakeup_repo.count_pending_async()
        assert count == 1

    @pytest.mark.asyncio
    async def test_sweep_marks_failed_on_fail_policy(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_task_record(
            on_timeout=TaskTimeoutAction.FAIL,
            updated_at_ago_seconds=200.0,
        )
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        event_log = AsyncMock(spec=EventLog)

        sweeper = StaleTaskSweeper(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            event_log=event_log,
        )
        await sweeper._sweep_once_async()

        assert task_repo.update_status_async.call_count == 2
