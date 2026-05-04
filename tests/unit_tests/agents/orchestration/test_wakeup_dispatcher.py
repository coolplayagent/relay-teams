# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Generator

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction, WakeupStatus
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.agents.orchestration.wakeup_dispatcher import WakeupDispatcher
from relay_teams.sessions.runs.event_log import EventLog


def _make_entry(**overrides: object) -> AgentWakeupEntry:
    defaults = {
        "wakeup_id": "wk_001",
        "task_id": "task_001",
        "trace_id": "trace_001",
        "session_id": "sess_001",
        "coalesce_key": "task_001:retry",
        "timeout_action": TaskTimeoutAction.RETRY,
        "timeout_seconds": 60.0,
        "attempt": 1,
        "max_attempts": 3,
        "status": WakeupStatus.PENDING,
        "enqueued_at": datetime.now(tz=timezone.utc),
    }
    defaults.update(overrides)
    return AgentWakeupEntry(**defaults)  # type: ignore[arg-type]


def _make_task_record(
    *,
    task_id: str = "task_001",
    status: TaskStatus = TaskStatus.TIMEOUT,
) -> TaskRecord:
    return TaskRecord(
        envelope=TaskEnvelope(
            task_id=task_id,
            session_id="sess_001",
            trace_id="trace_001",
            role_id="Crafter",
            objective="Test task",
            verification=VerificationPlan(),
        ),
        status=status,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


@pytest.fixture
def wakeup_repo() -> Generator[AgentWakeupRepository, None, None]:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield AgentWakeupRepository(db_path)


class TestWakeupDispatcher:
    @pytest.mark.asyncio
    async def test_dispatches_timeout_task(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        entry = _make_entry()
        await wakeup_repo.enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(
            return_value=_make_task_record(status=TaskStatus.TIMEOUT)
        )
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
        )
        await dispatcher._dispatch_one_async()

        assert await wakeup_repo.count_pending_async() == 0
        exec_service.execute.assert_called_once()
        call_kwargs = exec_service.execute.call_args
        assert call_kwargs.kwargs["role_id"] == "Crafter"
        assert call_kwargs.kwargs["task"].retry_attempt == 1

    @pytest.mark.asyncio
    async def test_expires_completed_task(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        entry = _make_entry()
        await wakeup_repo.enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(
            return_value=_make_task_record(status=TaskStatus.COMPLETED)
        )
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
        )
        await dispatcher._dispatch_one_async()

        assert await wakeup_repo.count_pending_async() == 0
        exec_service.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pending_does_nothing(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        task_repo = AsyncMock()
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
        )
        await dispatcher._dispatch_one_async()
        task_repo.get_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_expires_entry_when_task_not_found(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        entry = _make_entry()
        await wakeup_repo.enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(side_effect=KeyError("not found"))
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
        )
        await dispatcher._dispatch_one_async()

        exec_service.execute.assert_not_called()
        assert await wakeup_repo.count_pending_async() == 0

    @pytest.mark.asyncio
    async def test_dispatch_handles_execution_exception(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        entry = _make_entry()
        await wakeup_repo.enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(
            return_value=_make_task_record(status=TaskStatus.TIMEOUT)
        )
        exec_service = AsyncMock()
        exec_service.execute = AsyncMock(side_effect=RuntimeError("execution failed"))
        event_log = AsyncMock(spec=EventLog)

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
        )
        await dispatcher._dispatch_one_async()

        exec_service.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self) -> None:
        task_repo = AsyncMock()
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)
        wakeup_repo_mock = AsyncMock()

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo_mock,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
            poll_interval_seconds=600,
        )
        await dispatcher.start()
        assert dispatcher._background_task is not None
        await dispatcher.stop()
        assert dispatcher._background_task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        task_repo = AsyncMock()
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)
        wakeup_repo_mock = AsyncMock()

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo_mock,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
            poll_interval_seconds=600,
        )
        await dispatcher.start()
        first_task = dispatcher._background_task
        await dispatcher.start()
        assert dispatcher._background_task is first_task
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_stop_noop_when_not_started(self) -> None:
        task_repo = AsyncMock()
        exec_service = AsyncMock()
        event_log = AsyncMock(spec=EventLog)
        wakeup_repo_mock = AsyncMock()

        dispatcher = WakeupDispatcher(
            wakeup_repo=wakeup_repo_mock,
            task_repo=task_repo,
            task_execution_service=exec_service,
            event_log=event_log,
        )
        await dispatcher.stop()
        assert dispatcher._background_task is None
