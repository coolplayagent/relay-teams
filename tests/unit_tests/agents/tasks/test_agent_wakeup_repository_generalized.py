# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import (
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


@pytest.fixture
def wakeup_repo(tmp_path: object) -> AgentWakeupRepository:
    from pathlib import Path

    return AgentWakeupRepository(Path(str(tmp_path)) / "test_wakeups.db")


def _make_entry(**overrides: object) -> AgentWakeupEntry:
    defaults: dict[str, object] = dict(
        wakeup_id="wk_test_1",
        task_id="task_1",
        trace_id="trace_1",
        session_id="sess_1",
        coalesce_key="task_1:retry",
        timeout_action=TaskTimeoutAction.RETRY,
        timeout_seconds=60.0,
        attempt=1,
        max_attempts=3,
        status=WakeupStatus.PENDING,
        enqueued_at=datetime.now(tz=timezone.utc),
        wake_reason=WakeupReason.TIMEOUT_RETRY,
    )
    defaults.update(overrides)
    return AgentWakeupEntry(**defaults)  # type: ignore[arg-type]


class TestAgentWakeupRepositoryGeneralized:
    @pytest.mark.asyncio
    async def test_enqueue_generalized_with_reason(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        entry = _make_entry(
            wakeup_id="wk_gen_1",
            wake_reason=WakeupReason.APPROVAL_PASSED,
            target_role="Reviewer",
        )
        inserted = await wakeup_repo.enqueue_generalized_async(entry)
        assert inserted is True

    @pytest.mark.asyncio
    async def test_coalescing_same_key_rejected(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        entry1 = _make_entry(wakeup_id="wk_a", status=WakeupStatus.PENDING)
        entry2 = _make_entry(
            wakeup_id="wk_b",
            status=WakeupStatus.PENDING,
        )
        r1 = await wakeup_repo.enqueue_async(entry1)
        r2 = await wakeup_repo.enqueue_async(entry2)
        assert r1 is True
        assert r2 is False  # duplicate coalesce_key + status

    @pytest.mark.asyncio
    async def test_claim_pending_for_target(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        entry = _make_entry(
            wakeup_id="wk_target_1",
            target_role="Crafter",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        await wakeup_repo.enqueue_async(entry)
        claimed = await wakeup_repo.claim_pending_for_target_async("Crafter")
        assert claimed is not None
        assert claimed.target_role == "Crafter"

    @pytest.mark.asyncio
    async def test_claim_pending_for_target_miss(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        entry = _make_entry(
            wakeup_id="wk_target_2",
            target_role="Crafter",
        )
        await wakeup_repo.enqueue_async(entry)
        claimed = await wakeup_repo.claim_pending_for_target_async("Reviewer")
        assert claimed is None

    @pytest.mark.asyncio
    async def test_mark_expired_for_task(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        e1 = _make_entry(
            wakeup_id="wk_exp_1",
            coalesce_key="task_1:timeout",
            wake_reason=WakeupReason.TIMEOUT_RETRY,
        )
        e2 = _make_entry(
            wakeup_id="wk_exp_2",
            coalesce_key="task_1:orphan",
            wake_reason=WakeupReason.ORPHAN_RECOVERY,
        )
        await wakeup_repo.enqueue_async(e1)
        await wakeup_repo.enqueue_async(e2)
        count = await wakeup_repo.mark_expired_for_task_async(
            "task_1", WakeupReason.TIMEOUT_RETRY
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_list_pending_for_task(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        entry = _make_entry(
            wakeup_id="wk_list_1",
            coalesce_key="task_1:list",
        )
        await wakeup_repo.enqueue_async(entry)
        pending = await wakeup_repo.list_pending_for_task_async("task_1")
        assert len(pending) >= 1
