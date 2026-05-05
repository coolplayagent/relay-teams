# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.orchestration.harnesses import (
    control_harness as _ch_mod,
)
from relay_teams.agents.orchestration.harnesses.control_harness import (
    TaskControlHarness,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction, WakeupReason
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.event_log import EventLog


def _make_task(**overrides: object) -> TaskEnvelope:
    base: dict[str, object] = dict(
        task_id="task_ctrl_1",
        session_id="sess_1",
        trace_id="trace_1",
        objective="Test objective for control harness coverage.",
        verification={},
    )
    base.update(overrides)
    return TaskEnvelope(**base)  # type: ignore[arg-type]


@pytest.fixture()
def task_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_async = AsyncMock()
    repo.update_status_async = AsyncMock()
    repo.heartbeat_running_async = AsyncMock()
    repo.claim_task_async = AsyncMock(return_value=True)
    return repo


@pytest.fixture()
def wakeup_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.enqueue_async = AsyncMock(return_value=True)
    return repo


@pytest.fixture()
def artifact_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def event_log() -> EventLog:
    return EventLog(Path(":memory:"))


@pytest.fixture()
def harness(
    task_repo: AsyncMock,
    event_log: EventLog,
    wakeup_repo: AsyncMock,
    artifact_repo: MagicMock,
) -> TaskControlHarness:
    return TaskControlHarness(
        task_repo=task_repo,
        agent_repo=None,  # type: ignore[arg-type]
        run_runtime_repo=None,  # type: ignore[arg-type]
        event_bus=event_log,
        wakeup_repo=wakeup_repo,
        artifact_repo=artifact_repo,
    )


class TestTransitionToRunning:
    @pytest.mark.asyncio
    async def test_already_running(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        record = MagicMock()
        record.status = TaskStatus.RUNNING
        task_repo.get_async.return_value = record
        task = _make_task()
        await harness.transition_to_running(task, "inst_1", "role_1")
        task_repo.update_status_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_transitions_from_created(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        record = MagicMock()
        record.status = TaskStatus.CREATED
        task_repo.get_async.return_value = record
        task = _make_task()
        await harness.transition_to_running(task, "inst_1", "role_1")
        task_repo.update_status_async.assert_called_once_with(
            "task_ctrl_1", TaskStatus.RUNNING, assigned_instance_id="inst_1"
        )

    @pytest.mark.asyncio
    async def test_transitions_from_assigned(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        record = MagicMock()
        record.status = TaskStatus.ASSIGNED
        task_repo.get_async.return_value = record
        task = _make_task()
        await harness.transition_to_running(task, "inst_1", "role_1")
        task_repo.update_status_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        record = MagicMock()
        record.status = TaskStatus.COMPLETED
        task_repo.get_async.return_value = record
        task = _make_task()
        with pytest.raises(ValueError, match="cannot transition"):
            await harness.transition_to_running(task, "inst_1", "role_1")


class TestStartHeartbeat:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_interval(
        self, harness: TaskControlHarness
    ) -> None:
        task = _make_task()
        worker = asyncio.create_task(asyncio.sleep(10))
        try:
            result = await harness.start_heartbeat(task, "inst_1", "role_1", worker)
            assert result is None
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass  # Expected: awaiting a cancelled task during test cleanup.

    @pytest.mark.asyncio
    async def test_returns_task_when_interval_set(
        self, harness: TaskControlHarness
    ) -> None:
        task = _make_task(
            lifecycle={"heartbeat_interval_seconds": 0.01},
        )
        worker = asyncio.create_task(asyncio.sleep(10))
        try:
            hb = await harness.start_heartbeat(task, "inst_1", "role_1", worker)
            assert hb is not None
            assert isinstance(hb, asyncio.Task)
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass  # Expected: awaiting a cancelled task during test cleanup.

    @pytest.mark.asyncio
    async def test_heartbeat_calls_repo(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        task = _make_task(
            lifecycle={"heartbeat_interval_seconds": 0.01},
        )
        worker = asyncio.create_task(asyncio.sleep(0.05))
        try:
            hb = await harness.start_heartbeat(task, "inst_1", "role_1", worker)
            assert hb is not None
            await asyncio.sleep(0.05)
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass  # Expected: awaiting a cancelled task during test cleanup.
        assert task_repo.heartbeat_running_async.call_count >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_handles_repo_failure(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        task_repo.heartbeat_running_async.side_effect = RuntimeError("boom")
        task = _make_task(
            lifecycle={"heartbeat_interval_seconds": 0.01},
        )
        worker = asyncio.create_task(asyncio.sleep(0.05))
        try:
            hb = await harness.start_heartbeat(task, "inst_1", "role_1", worker)
            assert hb is not None
            await asyncio.sleep(0.05)
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass  # Expected: awaiting a cancelled task during test cleanup.


class TestHandleTimeout:
    @pytest.mark.asyncio
    async def test_worker_already_done_returns_result(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        task = _make_task()
        cancellation = asyncio.Event()
        result = TaskExecutionResult(
            output="done",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
        )

        async def _return_result() -> TaskExecutionResult:
            return result

        worker = asyncio.create_task(_return_result())
        await asyncio.sleep(0.01)
        out = await harness.handle_timeout(
            task, "inst_1", "role_1", worker, cancellation, 30.0
        )
        assert out.output == "done"

    @pytest.mark.asyncio
    async def test_worker_not_done_cancels(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        original_grace = _ch_mod.TIMEOUT_WORKER_CANCEL_GRACE_SECONDS
        _ch_mod.TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = 0.01
        try:
            task = _make_task(
                lifecycle={"on_timeout": TaskTimeoutAction.FAIL},
            )
            cancellation = asyncio.Event()

            async def _never_finish() -> TaskExecutionResult:
                await asyncio.sleep(100)
                return TaskExecutionResult(output="never")

            worker = asyncio.create_task(_never_finish())
            out = await harness.handle_timeout(
                task, "inst_1", "role_1", worker, cancellation, 1.0
            )
            assert out is not None
            assert "timed out" in (out.error_message or "").lower()
            task_repo.update_status_async.assert_called_once()
            assert cancellation.is_set()
        finally:
            _ch_mod.TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = original_grace

    @pytest.mark.asyncio
    async def test_worker_raises_exception(self, harness: TaskControlHarness) -> None:
        task = _make_task()
        cancellation = asyncio.Event()

        async def _fail() -> TaskExecutionResult:
            raise RuntimeError("worker error")

        worker = asyncio.create_task(_fail())
        await asyncio.sleep(0.01)
        out = await harness.handle_timeout(
            task, "inst_1", "role_1", worker, cancellation, 30.0
        )
        assert "worker error" in (out.error_message or "")


class TestClaimAndLease:
    @pytest.mark.asyncio
    async def test_claims_task(
        self, harness: TaskControlHarness, task_repo: AsyncMock
    ) -> None:
        task = _make_task()
        result = await harness.claim_and_lease(task, "inst_1", "token_abc")
        assert result is True
        task_repo.claim_task_async.assert_called_once()


class TestEnqueueRetryWakeup:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_wakeup_repo(
        self, task_repo: AsyncMock, event_log: EventLog, artifact_repo: MagicMock
    ) -> None:
        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=None,  # type: ignore[arg-type]
            run_runtime_repo=None,  # type: ignore[arg-type]
            event_bus=event_log,
            wakeup_repo=None,
            artifact_repo=artifact_repo,
        )
        task = _make_task()
        result = await harness.enqueue_retry_wakeup(task, 1)
        assert result is False

    @pytest.mark.asyncio
    async def test_enqueues_entry(
        self, harness: TaskControlHarness, wakeup_repo: AsyncMock
    ) -> None:
        task = _make_task()
        result = await harness.enqueue_retry_wakeup(task, 2)
        assert result is True
        wakeup_repo.enqueue_async.assert_called_once()
        entry = wakeup_repo.enqueue_async.call_args[0][0]
        assert isinstance(entry, AgentWakeupEntry)
        assert entry.attempt == 2
        assert entry.wake_reason == WakeupReason.TIMEOUT_RETRY


class TestAppendArtifactEntry:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_repo(
        self, task_repo: AsyncMock, event_log: EventLog, wakeup_repo: AsyncMock
    ) -> None:
        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=None,  # type: ignore[arg-type]
            run_runtime_repo=None,  # type: ignore[arg-type]
            event_bus=event_log,
            wakeup_repo=wakeup_repo,
            artifact_repo=None,
        )
        result = await harness.append_artifact_entry("task_1", MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_artifact(
        self, harness: TaskControlHarness, artifact_repo: MagicMock
    ) -> None:
        artifact_repo.get_artifact.return_value = None
        result = await harness.append_artifact_entry("task_1", MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_appends_and_returns_count(
        self, harness: TaskControlHarness, artifact_repo: MagicMock
    ) -> None:
        artifact1 = MagicMock()
        artifact1.entries = [MagicMock()]
        artifact2 = MagicMock()
        artifact2.entries = [MagicMock(), MagicMock()]
        artifact_repo.get_artifact.side_effect = [artifact1, artifact2]
        result = await harness.append_artifact_entry("task_1", MagicMock())
        assert result == 2


class TestMaybeEnqueueRetry:
    @pytest.mark.asyncio
    async def test_skips_when_not_retry(
        self, harness: TaskControlHarness, wakeup_repo: AsyncMock
    ) -> None:
        task = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.FAIL},
        )
        await harness._maybe_enqueue_retry(task)
        wakeup_repo.enqueue_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_max_attempts_reached(
        self, harness: TaskControlHarness, wakeup_repo: AsyncMock
    ) -> None:
        task = _make_task(
            retry_attempt=3,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 3,
            },
        )
        await harness._maybe_enqueue_retry(task)
        wakeup_repo.enqueue_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueues_when_retry_and_attempts_remain(
        self, harness: TaskControlHarness, wakeup_repo: AsyncMock
    ) -> None:
        task = _make_task(
            retry_attempt=1,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 3,
            },
        )
        await harness._maybe_enqueue_retry(task)
        wakeup_repo.enqueue_async.assert_called_once()
