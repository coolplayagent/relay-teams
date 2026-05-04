# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.task_repository import TaskRepository


@pytest.fixture
def task_repo(tmp_path: object) -> TaskRepository:
    from pathlib import Path

    return TaskRepository(Path(str(tmp_path)) / "test_tasks.db")


def _minimal_envelope(**overrides: object) -> TaskEnvelope:
    defaults: dict[str, object] = dict(
        task_id="task_claim_1",
        session_id="sess_1",
        trace_id="trace_1",
        objective="Test objective for lease operations.",
        verification={},
    )
    defaults.update(overrides)
    return TaskEnvelope(**defaults)  # type: ignore[arg-type]


class TestTaskRepositoryLease:
    @pytest.mark.asyncio
    async def test_claim_task_created(self, task_repo: TaskRepository) -> None:
        envelope = _minimal_envelope(task_id="task_claim_created")
        task_repo.create(envelope)
        claimed = await task_repo.claim_task_async(
            task_id="task_claim_created",
            lease_owner="instance_1",
            claim_token="token_abc",
            lease_duration_seconds=3600.0,
        )
        assert claimed is True
        record = task_repo.get("task_claim_created")
        assert record.envelope.lease_owner == "instance_1"
        assert record.envelope.claim_token == "token_abc"
        assert record.envelope.lease_expires_at is not None

    @pytest.mark.asyncio
    async def test_claim_task_running_conflict(self, task_repo: TaskRepository) -> None:
        envelope = _minimal_envelope(task_id="task_claim_running")
        task_repo.create(envelope)
        task_repo.update_status(
            "task_claim_running",
            TaskStatus.RUNNING,
            assigned_instance_id="other_instance",
        )
        claimed = await task_repo.claim_task_async(
            task_id="task_claim_running",
            lease_owner="instance_2",
            claim_token="token_def",
            lease_duration_seconds=3600.0,
        )
        assert claimed is False

    @pytest.mark.asyncio
    async def test_claim_task_not_found(self, task_repo: TaskRepository) -> None:
        claimed = await task_repo.claim_task_async(
            task_id="nonexistent",
            lease_owner="instance_1",
            claim_token="token_ghi",
            lease_duration_seconds=3600.0,
        )
        assert claimed is False

    @pytest.mark.asyncio
    async def test_find_expired_leases(self, task_repo: TaskRepository) -> None:
        envelope = _minimal_envelope(task_id="task_expired_lease")
        task_repo.create(envelope)
        task_repo.update_status(
            "task_expired_lease",
            TaskStatus.RUNNING,
            assigned_instance_id="inst_1",
        )
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        record = task_repo.get("task_expired_lease")
        updated_envelope = record.envelope.model_copy(update={"lease_expires_at": past})
        task_repo.update_envelope("task_expired_lease", updated_envelope)

        now = datetime.now(tz=timezone.utc)
        expired = await task_repo.find_expired_leases_async(now)
        task_ids = [r.envelope.task_id for r in expired]
        assert "task_expired_lease" in task_ids

    @pytest.mark.asyncio
    async def test_find_expired_leases_future_not_returned(
        self, task_repo: TaskRepository
    ) -> None:
        envelope = _minimal_envelope(task_id="task_future_lease")
        task_repo.create(envelope)
        task_repo.update_status(
            "task_future_lease",
            TaskStatus.RUNNING,
            assigned_instance_id="inst_1",
        )
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        record = task_repo.get("task_future_lease")
        updated_envelope = record.envelope.model_copy(
            update={"lease_expires_at": future}
        )
        task_repo.update_envelope("task_future_lease", updated_envelope)

        now = datetime.now(tz=timezone.utc)
        expired = await task_repo.find_expired_leases_async(now)
        task_ids = [r.envelope.task_id for r in expired]
        assert "task_future_lease" not in task_ids
