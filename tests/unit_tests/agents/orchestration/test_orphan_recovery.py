# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Generator

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskLifecyclePolicy,
    TaskRecord,
    VerificationPlan,
)
from relay_teams.agents.orchestration.orphan_recovery_service import (
    OrphanRecoveryService,
)
from relay_teams.sessions.runs.event_log import EventLog


def _make_orphan_task_record(
    *,
    task_id: str = "orphan_1",
    on_timeout: TaskTimeoutAction = TaskTimeoutAction.RETRY,
    retry_attempt: int = 0,
) -> TaskRecord:
    return TaskRecord(
        envelope=TaskEnvelope(
            task_id=task_id,
            session_id="sess_1",
            trace_id="trace_1",
            role_id="Crafter",
            objective="Orphan task",
            retry_attempt=retry_attempt,
            lifecycle=TaskLifecyclePolicy(
                on_timeout=on_timeout,
                max_retry_attempts=3,
            ),
            verification=VerificationPlan(),
        ),
        status=TaskStatus.RUNNING,
        assigned_instance_id="instance_dead",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


@pytest.fixture
def wakeup_repo() -> Generator[AgentWakeupRepository, None, None]:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield AgentWakeupRepository(db_path)


class TestOrphanRecovery:
    @pytest.mark.asyncio
    async def test_orphan_retry_enqueued(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_orphan_task_record(on_timeout=TaskTimeoutAction.RETRY)
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        dead_instance = MagicMock()
        dead_instance.status = "failed"
        agent_repo = AsyncMock(spec=AgentInstanceRepository)
        agent_repo.get_instance_async = AsyncMock(return_value=dead_instance)
        event_log = AsyncMock(spec=EventLog)

        service = OrphanRecoveryService(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            agent_repo=agent_repo,
            event_log=event_log,
        )
        recovered = await service.recover_orphans_async()

        assert recovered == 1
        count = await wakeup_repo.count_pending_async()
        assert count == 1

    @pytest.mark.asyncio
    async def test_orphan_fail_marked_failed(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_orphan_task_record(on_timeout=TaskTimeoutAction.FAIL)
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        dead_instance = MagicMock()
        dead_instance.status = "failed"
        agent_repo = AsyncMock(spec=AgentInstanceRepository)
        agent_repo.get_instance_async = AsyncMock(return_value=dead_instance)
        event_log = AsyncMock(spec=EventLog)

        service = OrphanRecoveryService(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            agent_repo=agent_repo,
            event_log=event_log,
        )
        recovered = await service.recover_orphans_async()

        assert recovered == 1
        task_repo.update_status_async.assert_called()
        timeout_call = task_repo.update_status_async.call_args_list[0]
        assert timeout_call[0][1] == TaskStatus.TIMEOUT
        fail_call = task_repo.update_status_async.call_args_list[1]
        assert fail_call[0][1] == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_orphan_list_failure_returns_zero(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(side_effect=RuntimeError("db error"))
        task_repo.update_status_async = AsyncMock()
        agent_repo = AsyncMock(spec=AgentInstanceRepository)
        event_log = AsyncMock(spec=EventLog)

        service = OrphanRecoveryService(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            agent_repo=agent_repo,
            event_log=event_log,
        )
        result = await service.recover_orphans_async()

        assert result == 0
        task_repo.update_status_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_skips_task_without_instance_id(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = TaskRecord(
            envelope=TaskEnvelope(
                task_id="no_instance",
                session_id="sess_1",
                trace_id="trace_1",
                role_id="Crafter",
                objective="No instance",
                retry_attempt=0,
                lifecycle=TaskLifecyclePolicy(
                    on_timeout=TaskTimeoutAction.RETRY,
                    max_retry_attempts=3,
                ),
                verification=VerificationPlan(),
            ),
            status=TaskStatus.RUNNING,
            assigned_instance_id=None,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        agent_repo = AsyncMock(spec=AgentInstanceRepository)
        event_log = AsyncMock(spec=EventLog)

        service = OrphanRecoveryService(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            agent_repo=agent_repo,
            event_log=event_log,
        )
        result = await service.recover_orphans_async()

        assert result == 0
        agent_repo.get_instance_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_skips_when_instance_unknown(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_orphan_task_record()
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        agent_repo = AsyncMock(spec=AgentInstanceRepository)
        agent_repo.get_instance_async = AsyncMock(side_effect=KeyError("no instance"))
        event_log = AsyncMock(spec=EventLog)

        service = OrphanRecoveryService(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            agent_repo=agent_repo,
            event_log=event_log,
        )
        result = await service.recover_orphans_async()

        assert result == 1

    @pytest.mark.asyncio
    async def test_orphan_max_retry_marks_failed(
        self,
        wakeup_repo: AgentWakeupRepository,
    ) -> None:
        record = _make_orphan_task_record(
            on_timeout=TaskTimeoutAction.RETRY,
            retry_attempt=3,
        )
        task_repo = AsyncMock()
        task_repo.list_running_async = AsyncMock(return_value=(record,))
        task_repo.update_status_async = AsyncMock()
        dead_instance = MagicMock()
        dead_instance.status = "failed"
        agent_repo = AsyncMock(spec=AgentInstanceRepository)
        agent_repo.get_instance_async = AsyncMock(return_value=dead_instance)
        event_log = AsyncMock(spec=EventLog)

        service = OrphanRecoveryService(
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
            agent_repo=agent_repo,
            event_log=event_log,
        )
        result = await service.recover_orphans_async()

        assert result == 1
        fail_calls = [
            c
            for c in task_repo.update_status_async.call_args_list
            if c[0][1] == TaskStatus.FAILED
        ]
        assert len(fail_calls) >= 1
