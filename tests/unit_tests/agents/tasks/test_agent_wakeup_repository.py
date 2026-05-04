# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import TaskTimeoutAction, WakeupStatus
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


@pytest.fixture
def repo() -> Generator[AgentWakeupRepository, None, None]:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_wakeups.db"
        yield AgentWakeupRepository(db_path)


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


class TestAgentWakeupRepository:
    @pytest.mark.asyncio
    async def test_enqueue_and_count(self, repo: AgentWakeupRepository) -> None:
        entry = _make_entry()
        inserted = await repo.enqueue_async(entry)
        assert inserted is True
        count = await repo.count_pending_async()
        assert count == 1

    @pytest.mark.asyncio
    async def test_enqueue_coalesce_dedup(self, repo: AgentWakeupRepository) -> None:
        entry1 = _make_entry(wakeup_id="wk_001")
        entry2 = _make_entry(wakeup_id="wk_002")
        assert await repo.enqueue_async(entry1) is True
        assert await repo.enqueue_async(entry2) is False
        assert await repo.count_pending_async() == 1

    @pytest.mark.asyncio
    async def test_claim_next_pending(self, repo: AgentWakeupRepository) -> None:
        now = datetime.now(tz=timezone.utc)
        entry = _make_entry(enqueued_at=now)
        await repo.enqueue_async(entry)
        claimed = await repo.claim_next_pending_async()
        assert claimed is not None
        assert claimed.wakeup_id == "wk_001"
        assert claimed.status == WakeupStatus.PENDING
        count = await repo.count_pending_async()
        assert count == 0

    @pytest.mark.asyncio
    async def test_claim_empty(self, repo: AgentWakeupRepository) -> None:
        claimed = await repo.claim_next_pending_async()
        assert claimed is None

    @pytest.mark.asyncio
    async def test_complete(self, repo: AgentWakeupRepository) -> None:
        entry = _make_entry()
        await repo.enqueue_async(entry)
        claimed = await repo.claim_next_pending_async()
        assert claimed is not None
        await repo.complete_async(claimed.wakeup_id)
        pending = await repo.list_pending_for_task_async("task_001")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_expire(self, repo: AgentWakeupRepository) -> None:
        entry = _make_entry()
        await repo.enqueue_async(entry)
        claimed = await repo.claim_next_pending_async()
        assert claimed is not None
        await repo.expire_async(claimed.wakeup_id)
        pending = await repo.list_pending_for_task_async("task_001")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_list_pending_for_task(self, repo: AgentWakeupRepository) -> None:
        now = datetime.now(tz=timezone.utc)
        e1 = _make_entry(
            wakeup_id="wk_a",
            task_id="t1",
            coalesce_key="t1:retry",
            enqueued_at=now,
        )
        e2 = _make_entry(
            wakeup_id="wk_b",
            task_id="t2",
            coalesce_key="t2:retry",
            enqueued_at=now,
        )
        await repo.enqueue_async(e1)
        await repo.enqueue_async(e2)
        pending_t1 = await repo.list_pending_for_task_async("t1")
        assert len(pending_t1) == 1
        assert pending_t1[0].task_id == "t1"

    @pytest.mark.asyncio
    async def test_reopen_existing_db(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_reopen.db"
            repo1 = AgentWakeupRepository(db_path)
            entry = _make_entry()
            await repo1.enqueue_async(entry)

            repo2 = AgentWakeupRepository(db_path)
            count = await repo2.count_pending_async()
            assert count == 1

    @pytest.mark.asyncio
    async def test_claim_next_pending_returns_none_when_empty(
        self,
        repo: AgentWakeupRepository,
    ) -> None:
        result = await repo.claim_next_pending_async()
        assert result is None
