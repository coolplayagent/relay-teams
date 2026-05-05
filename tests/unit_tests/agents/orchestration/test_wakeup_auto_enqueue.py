# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import (
    TaskStatus,
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.agents.orchestration.wakeup_auto_enqueue import (
    enqueue_approval_wakeups,
    enqueue_dependency_wakeups,
)
from relay_teams.agents.orchestration.wakeup_dispatcher import WakeupDispatcher
from relay_teams.sessions.runs.event_log import EventLog


# ── helpers ──────────────────────────────────────────────────────────


def _make_entry(**overrides: object) -> AgentWakeupEntry:
    defaults: dict[str, object] = {
        "wakeup_id": "wk_001",
        "task_id": "task_001",
        "trace_id": "trace_001",
        "session_id": "sess_001",
        "coalesce_key": "task_001:test",
        "timeout_action": TaskTimeoutAction.RETRY,
        "timeout_seconds": 60.0,
        "attempt": 1,
        "max_attempts": 3,
        "status": WakeupStatus.PENDING,
        "enqueued_at": datetime.now(tz=timezone.utc),
        "wake_reason": WakeupReason.TIMEOUT_RETRY,
    }
    defaults.update(overrides)
    return AgentWakeupEntry(**defaults)  # type: ignore[arg-type]


def _make_task_envelope(
    *,
    task_id: str = "task_001",
    trace_id: str = "trace_001",
    session_id: str = "sess_001",
    role_id: str = "Crafter",
    depends_on_task_ids: tuple[str, ...] = (),
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id,
        trace_id=trace_id,
        session_id=session_id,
        role_id=role_id,
        objective="Test objective",
        depends_on_task_ids=depends_on_task_ids,
        verification=VerificationPlan(),
    )


def _make_task_record(
    *,
    task_id: str = "task_001",
    trace_id: str = "trace_001",
    session_id: str = "sess_001",
    role_id: str = "Crafter",
    status: TaskStatus = TaskStatus.TIMEOUT,
    depends_on_task_ids: tuple[str, ...] = (),
) -> TaskRecord:
    return TaskRecord(
        envelope=TaskEnvelope(
            task_id=task_id,
            trace_id=trace_id,
            session_id=session_id,
            role_id=role_id,
            objective="Test objective",
            depends_on_task_ids=depends_on_task_ids,
            verification=VerificationPlan(),
        ),
        status=status,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def wakeup_repo() -> Generator[AgentWakeupRepository, None, None]:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield AgentWakeupRepository(db_path)


# ── coalesce_and_enqueue tests ───────────────────────────────────────


class TestCoalesceAndEnqueue:
    @pytest.mark.asyncio
    async def test_coalesce_same_reason_merged(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Same (task_id, wake_reason) should merge into one entry."""
        entry1 = _make_entry(
            wakeup_id="wk_a",
            task_id="task_001",
            coalesce_key="task_001:dep:task_x",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        entry2 = _make_entry(
            wakeup_id="wk_b",
            task_id="task_001",
            coalesce_key="task_001:dep:task_y",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        inserted1 = await wakeup_repo.coalesce_and_enqueue_async(entry1)
        assert inserted1 is True

        inserted2 = await wakeup_repo.coalesce_and_enqueue_async(entry2)
        assert inserted2 is False  # merged, not newly inserted

        pending = await wakeup_repo.count_pending_async()
        assert pending == 1

    @pytest.mark.asyncio
    async def test_coalesce_different_reason_separate(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Different wake_reason for the same task_id should be separate."""
        entry1 = _make_entry(
            wakeup_id="wk_a",
            task_id="task_001",
            coalesce_key="task_001:dep",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        entry2 = _make_entry(
            wakeup_id="wk_b",
            task_id="task_001",
            coalesce_key="task_001:timeout",
            wake_reason=WakeupReason.TIMEOUT_RETRY,
        )
        assert await wakeup_repo.coalesce_and_enqueue_async(entry1) is True
        assert await wakeup_repo.coalesce_and_enqueue_async(entry2) is True

        pending = await wakeup_repo.count_pending_async()
        assert pending == 2

    @pytest.mark.asyncio
    async def test_coalesce_updates_existing_entry(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Merged entry should have the newer coalesce_key."""
        entry1 = _make_entry(
            wakeup_id="wk_a",
            task_id="task_001",
            coalesce_key="old_key",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        entry2 = _make_entry(
            wakeup_id="wk_b",
            task_id="task_001",
            coalesce_key="new_key",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
            source_event_type="task_completed",
            source_trigger_id="task_x",
        )
        await wakeup_repo.coalesce_and_enqueue_async(entry1)
        await wakeup_repo.coalesce_and_enqueue_async(entry2)

        claimed = await wakeup_repo.claim_next_pending_async()
        assert claimed is not None
        assert claimed.coalesce_key == "new_key"
        assert claimed.source_event_type == "task_completed"
        assert claimed.source_trigger_id == "task_x"

    @pytest.mark.asyncio
    async def test_wakeup_entry_source_event_type(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """source_event_type and source_trigger_id persist correctly."""
        entry = _make_entry(
            wakeup_id="wk_src",
            task_id="task_002",
            wake_reason=WakeupReason.APPROVAL_PASSED,
        )
        entry = entry.model_copy(
            update={
                "source_event_type": "approval_passed",
                "source_trigger_id": "gate_99",
            }
        )
        await wakeup_repo.coalesce_and_enqueue_async(entry)

        claimed = await wakeup_repo.claim_next_pending_async()
        assert claimed is not None
        assert claimed.source_event_type == "approval_passed"
        assert claimed.source_trigger_id == "gate_99"


# ── enqueue_dependency_wakeups tests ─────────────────────────────────


class TestEnqueueDependencyWakeups:
    @pytest.mark.asyncio
    async def test_enqueue_dependency_wakeups_finds_downstream(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Task completion enqueues wakes for downstream dependants."""
        completed = _make_task_envelope(
            task_id="task_A",
            trace_id="trace_001",
        )
        downstream = _make_task_record(
            task_id="task_B",
            trace_id="trace_001",
            status=TaskStatus.TIMEOUT,
            depends_on_task_ids=("task_A",),
        )
        task_repo = AsyncMock()
        task_repo.list_by_trace_async = AsyncMock(return_value=(downstream,))

        count = await enqueue_dependency_wakeups(
            completed_task_id="task_A",
            completed_task_envelope=completed,
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count == 1
        pending = await wakeup_repo.count_pending_async()
        assert pending == 1

    @pytest.mark.asyncio
    async def test_enqueue_dependency_wakeups_coalesces_duplicates(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Second completion of same upstream should coalesce."""
        completed = _make_task_envelope(
            task_id="task_A",
            trace_id="trace_001",
        )
        downstream = _make_task_record(
            task_id="task_B",
            trace_id="trace_001",
            status=TaskStatus.TIMEOUT,
            depends_on_task_ids=("task_A",),
        )
        task_repo = AsyncMock()
        task_repo.list_by_trace_async = AsyncMock(return_value=(downstream,))

        await enqueue_dependency_wakeups(
            completed_task_id="task_A",
            completed_task_envelope=completed,
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        count2 = await enqueue_dependency_wakeups(
            completed_task_id="task_A",
            completed_task_envelope=completed,
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count2 == 0  # coalesced
        pending = await wakeup_repo.count_pending_async()
        assert pending == 1

    @pytest.mark.asyncio
    async def test_enqueue_dependency_wakeups_skips_non_dependants(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Tasks not depending on the completed task are skipped."""
        completed = _make_task_envelope(
            task_id="task_A",
            trace_id="trace_001",
        )
        downstream = _make_task_record(
            task_id="task_B",
            trace_id="trace_001",
            status=TaskStatus.TIMEOUT,
            depends_on_task_ids=("task_C",),  # depends on C, not A
        )
        task_repo = AsyncMock()
        task_repo.list_by_trace_async = AsyncMock(return_value=(downstream,))

        count = await enqueue_dependency_wakeups(
            completed_task_id="task_A",
            completed_task_envelope=completed,
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_enqueue_dependency_wakeups_skips_completed_task_itself(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """The completed task itself should not get a wakeup."""
        completed = _make_task_envelope(
            task_id="task_A",
            trace_id="trace_001",
        )
        # Task A depends on itself is nonsense, but guard against it anyway
        downstream = _make_task_record(
            task_id="task_A",  # same as completed
            trace_id="trace_001",
            status=TaskStatus.COMPLETED,
            depends_on_task_ids=("task_A",),
        )
        task_repo = AsyncMock()
        task_repo.list_by_trace_async = AsyncMock(return_value=(downstream,))

        count = await enqueue_dependency_wakeups(
            completed_task_id="task_A",
            completed_task_envelope=completed,
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_enqueue_dependency_wakeups_handles_repo_exception(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Should return 0 when task_repo.list_by_trace_async raises."""
        completed = _make_task_envelope(
            task_id="task_A",
            trace_id="trace_001",
        )
        task_repo = AsyncMock()
        task_repo.list_by_trace_async = AsyncMock(side_effect=RuntimeError("boom"))

        count = await enqueue_dependency_wakeups(
            completed_task_id="task_A",
            completed_task_envelope=completed,
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count == 0


# ── enqueue_approval_wakeups tests ────────────────────────────────────


class TestEnqueueApprovalWakeups:
    @pytest.mark.asyncio
    async def test_approval_passed_enqueues_wake(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Approved gate enqueues APPROVAL_PASSED wake for STOPPED task."""
        task_record = _make_task_record(
            task_id="task_gated",
            status=TaskStatus.STOPPED,
        )
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(return_value=task_record)

        count = await enqueue_approval_wakeups(
            task_id="task_gated",
            trace_id="trace_001",
            session_id="sess_001",
            gate_id="gate_1",
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count == 1
        pending = await wakeup_repo.count_pending_async()
        assert pending == 1

        claimed = await wakeup_repo.claim_next_pending_async()
        assert claimed is not None
        assert claimed.wake_reason == WakeupReason.APPROVAL_PASSED
        assert claimed.source_event_type == "approval_passed"
        assert claimed.source_trigger_id == "gate_1"

    @pytest.mark.asyncio
    async def test_approval_skips_non_stopped_task(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """Only STOPPED tasks get APPROVAL_PASSED wakes."""
        task_record = _make_task_record(
            task_id="task_running",
            status=TaskStatus.RUNNING,
        )
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(return_value=task_record)

        count = await enqueue_approval_wakeups(
            task_id="task_running",
            trace_id="trace_001",
            session_id="sess_001",
            gate_id="gate_1",
            task_repo=task_repo,
            wakeup_repo=wakeup_repo,
        )
        assert count == 0


# ── dispatcher extended status tests ──────────────────────────────────


class TestWakeupDispatcherExtended:
    @pytest.mark.asyncio
    async def test_dispatcher_handles_created_status(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """WakeupDispatcher now dispatches CREATED tasks (was only TIMEOUT/STOPPED)."""
        entry = _make_entry(
            wakeup_id="wk_created",
            task_id="task_created",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        await wakeup_repo.coalesce_and_enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(
            return_value=_make_task_record(
                task_id="task_created",
                status=TaskStatus.CREATED,
            )
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

        exec_service.execute.assert_called_once()
        assert await wakeup_repo.count_pending_async() == 0

    @pytest.mark.asyncio
    async def test_dispatcher_handles_assigned_status(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """WakeupDispatcher dispatches ASSIGNED tasks."""
        entry = _make_entry(
            wakeup_id="wk_assigned",
            task_id="task_assigned",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        await wakeup_repo.coalesce_and_enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(
            return_value=_make_task_record(
                task_id="task_assigned",
                status=TaskStatus.ASSIGNED,
            )
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

        exec_service.execute.assert_called_once()
        assert await wakeup_repo.count_pending_async() == 0

    @pytest.mark.asyncio
    async def test_dispatcher_expires_failed_task(
        self, wakeup_repo: AgentWakeupRepository
    ) -> None:
        """FAILED tasks should be expired, not dispatched."""
        entry = _make_entry(
            wakeup_id="wk_failed",
            task_id="task_failed",
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
        )
        await wakeup_repo.coalesce_and_enqueue_async(entry)

        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock(
            return_value=_make_task_record(
                task_id="task_failed",
                status=TaskStatus.FAILED,
            )
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

        exec_service.execute.assert_not_called()
        assert await wakeup_repo.count_pending_async() == 0  # expired
