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
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.tasks.models import TaskHandoff
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPauseError
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimePhase


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
            result = harness.start_heartbeat(task, "inst_1", worker)
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
            hb = harness.start_heartbeat(task, "inst_1", worker)
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
            hb = harness.start_heartbeat(task, "inst_1", worker)
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
            hb = harness.start_heartbeat(task, "inst_1", worker)
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


# ── Phase 2 (AO-1-C1) tests ──────────────────────────────────────────


class TestTransitionTaskToRunning:
    @pytest.mark.asyncio
    async def test_normal_flow_coordinator(self) -> None:
        """Coordinator flow: mark RUNNING + ensure/update + emit."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        run_runtime_repo.update_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task()
        await harness.transition_task_to_running(
            task, "inst_1", "role_1", is_coordinator=True
        )

        agent_repo.mark_status_async.assert_called_once_with(
            "inst_1", InstanceStatus.RUNNING
        )
        task_repo.update_status_async.assert_called_once()
        run_runtime_repo.ensure_async.assert_called_once()
        run_runtime_repo.update_async.assert_called_once()
        _, kwargs = run_runtime_repo.update_async.call_args
        assert kwargs["phase"] == RunRuntimePhase.COORDINATOR_RUNNING
        assert kwargs["active_subagent_instance_id"] is None
        event_bus.emit_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_normal_flow_subagent(self) -> None:
        """Subagent flow: uses SUBAGENT_RUNNING phase."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        run_runtime_repo.update_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task(parent_task_id="parent_1")
        await harness.transition_task_to_running(
            task, "inst_2", "role_2", is_coordinator=False
        )

        _, kwargs = run_runtime_repo.update_async.call_args
        assert kwargs["phase"] == RunRuntimePhase.SUBAGENT_RUNNING
        assert kwargs["active_subagent_instance_id"] == "inst_2"

    @pytest.mark.asyncio
    async def test_root_task_id_fallbacks_to_task_id(self) -> None:
        """When parent_task_id is None, root_task_id falls back to task_id."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        run_runtime_repo.update_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task()
        await harness.transition_task_to_running(
            task, "inst_3", "role_3", is_coordinator=True
        )

        _, kwargs = run_runtime_repo.ensure_async.call_args
        assert kwargs["root_task_id"] == task.task_id


class TestInitializeTaskArtifact:
    def test_no_artifact_repo_returns_immediately(self) -> None:
        """When artifact_repo is None, the method is a no-op."""
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            artifact_repo=None,
        )
        task = _make_task()
        harness.initialize_task_artifact(task, "inst_1", "role_1")

    def test_creates_artifact_and_entry(self) -> None:
        """Normal flow: ensure_artifact + append_entry."""
        artifact_repo = MagicMock()
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            artifact_repo=artifact_repo,
        )
        task = _make_task()
        harness.initialize_task_artifact(task, "inst_1", "role_1")

        artifact_repo.ensure_artifact.assert_called_once()
        artifact_repo.append_entry.assert_called_once()


class TestPublishGuardrailReport:
    @pytest.mark.asyncio
    async def test_no_shared_store_returns_immediately(self) -> None:
        """When shared_store is None, the method is a no-op."""
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            shared_store=None,
        )
        task = _make_task()
        await harness.publish_guardrail_report(task, "inst_1", "role_1")

    @pytest.mark.asyncio
    async def test_generation_failure_logs_warning_not_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When report generation fails, it logs warning but does not propagate."""

        async def _fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("guardrail generation failed")

        monkeypatch.setattr(_ch_mod, "generate_runtime_guardrail_report_async", _fail)
        shared_store = MagicMock()
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            shared_store=shared_store,
        )
        task = _make_task()
        await harness.publish_guardrail_report(task, "inst_1", "role_1")


class TestCompleteTaskTimeout:
    @pytest.mark.asyncio
    async def test_fail_action_returns_result(self) -> None:
        """FAIL on_timeout: returns TaskExecutionResult, no RecoverableRunPauseError."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock()
        record = MagicMock()
        record.envelope = _make_task(lifecycle={"on_timeout": TaskTimeoutAction.FAIL})
        task_repo.get_async.return_value = record
        task_repo.update_envelope_async = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.FAIL, "timeout_seconds": 30.0},
        )
        result = await harness.complete_task_timeout(
            task, "inst_1", "role_1", timeout_seconds=30.0
        )

        assert isinstance(result, TaskExecutionResult)
        assert result.error_code == "task_timeout"
        assert "timed out" in (result.error_message or "").lower()
        task_repo.update_status_async.assert_called_once()
        agent_repo.mark_status_async.assert_called_once()
        run_runtime_repo.ensure_async.assert_called_once()
        event_bus.emit_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_action_raises_recoverable_pause(self) -> None:
        """STOP/RETRY/HUMAN_GATE: raises RecoverableRunPauseError."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock()
        record = MagicMock()
        record.envelope = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.HUMAN_GATE}
        )
        task_repo.get_async.return_value = record
        task_repo.update_envelope_async = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task(
            lifecycle={
                "on_timeout": TaskTimeoutAction.HUMAN_GATE,
                "timeout_seconds": 30.0,
            },
        )
        with pytest.raises(RecoverableRunPauseError):
            await harness.complete_task_timeout(
                task, "inst_1", "role_1", timeout_seconds=30.0
            )

    @pytest.mark.asyncio
    async def test_retry_enqueues_wakeup(self) -> None:
        """RETRY action: enqueues a wakeup entry."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock()
        record = MagicMock()
        record.envelope = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.RETRY, "max_retry_attempts": 3},
        )
        task_repo.get_async.return_value = record
        task_repo.update_envelope_async = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()
        wakeup_repo = AsyncMock()
        wakeup_repo.enqueue_async = AsyncMock(return_value=True)

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
            wakeup_repo=wakeup_repo,
        )
        task = _make_task(
            retry_attempt=1,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 3,
                "timeout_seconds": 30.0,
            },
        )
        with pytest.raises(RecoverableRunPauseError):
            await harness.complete_task_timeout(
                task, "inst_1", "role_1", timeout_seconds=30.0
            )

        wakeup_repo.enqueue_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_over_max_attempts_no_enqueue(self) -> None:
        """RETRY action with retry_attempt >= max_attempts: no wakeup."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock()
        record = MagicMock()
        record.envelope = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.RETRY, "max_retry_attempts": 2},
        )
        task_repo.get_async.return_value = record
        task_repo.update_envelope_async = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()
        wakeup_repo = AsyncMock()
        wakeup_repo.enqueue_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
            wakeup_repo=wakeup_repo,
        )
        task = _make_task(
            retry_attempt=2,
            lifecycle={
                "on_timeout": TaskTimeoutAction.RETRY,
                "max_retry_attempts": 2,
                "timeout_seconds": 30.0,
            },
        )
        with pytest.raises(RecoverableRunPauseError):
            await harness.complete_task_timeout(
                task, "inst_1", "role_1", timeout_seconds=30.0
            )

        wakeup_repo.enqueue_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_handoff_model_copy(self) -> None:
        """When task has a pre-existing handoff, model_copy() logic is used."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock()
        existing_handoff = TaskHandoff(reason="previous", incomplete=("item1",))
        record = MagicMock()
        record.envelope = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.FAIL, "timeout_seconds": 30.0},
            handoff=existing_handoff,
        )
        task_repo.get_async.return_value = record
        task_repo.update_envelope_async = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.FAIL, "timeout_seconds": 30.0},
            handoff=existing_handoff,
        )
        result = await harness.complete_task_timeout(
            task, "inst_1", "role_1", timeout_seconds=30.0
        )

        assert isinstance(result, TaskExecutionResult)
        task_repo.update_envelope_async.assert_called_once()


class TestCompleteTimeoutAfterWorkerCancel:
    @pytest.mark.asyncio
    async def test_worker_already_done_returns_result(self) -> None:
        """Worker was already done; returns its result."""
        task_repo = MagicMock()
        agent_repo = MagicMock()
        run_runtime_repo = MagicMock()
        event_bus = MagicMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task()
        cancellation = asyncio.Event()
        expected = TaskExecutionResult(output="done")

        async def _done() -> TaskExecutionResult:
            return expected

        worker = asyncio.create_task(_done())
        await asyncio.sleep(0.01)

        result = await harness.complete_timeout_after_worker_cancel(
            task, "inst_1", "role_1", 10.0, worker, cancellation
        )
        assert result is expected
        assert cancellation.is_set()

    @pytest.mark.asyncio
    async def test_cancels_worker_and_calls_complete_timeout(self) -> None:
        """Worker is still running; cancels and delegates to complete_task_timeout."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_async = AsyncMock()
        record = MagicMock()
        record.envelope = _make_task(lifecycle={"on_timeout": TaskTimeoutAction.FAIL})
        task_repo.get_async.return_value = record
        task_repo.update_envelope_async = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        run_runtime_repo.ensure_async = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
        )
        task = _make_task(
            lifecycle={"on_timeout": TaskTimeoutAction.FAIL, "timeout_seconds": 30.0},
        )
        cancellation = asyncio.Event()

        async def _never_finish() -> TaskExecutionResult:
            await asyncio.sleep(100)
            return TaskExecutionResult(output="never")

        worker = asyncio.create_task(_never_finish())

        result = await harness.complete_timeout_after_worker_cancel(
            task, "inst_1", "role_1", 30.0, worker, cancellation
        )
        assert isinstance(result, TaskExecutionResult)
        assert cancellation.is_set()


class TestPersistCancelledExecution:
    @pytest.mark.asyncio
    async def test_no_control_manager_fallback(self) -> None:
        """When run_control_manager is None, marks FAILED directly."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        task_repo.update_status_async = AsyncMock()
        run_runtime_repo = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
            run_control_manager=None,
        )
        task = _make_task()
        stopped, paused_subagent = await harness.persist_cancelled_execution(
            task,
            "inst_1",
            "role_1",
            is_coordinator=True,
        )
        assert stopped is False
        assert paused_subagent is False
        task_repo.update_status_async.assert_called_once()
        agent_repo.mark_status_async.assert_called_once_with(
            "inst_1",
            InstanceStatus.FAILED,
        )
        event_bus.emit_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_coordinator_stopped(self) -> None:
        """Run control manager reports full run stop; no subagent pause."""
        agent_repo = AsyncMock()
        agent_repo.mark_status_async = AsyncMock()
        task_repo = AsyncMock()
        run_runtime_repo = AsyncMock()
        event_bus = MagicMock()
        event_bus.emit_async = AsyncMock()
        run_control_manager = MagicMock()
        run_control_manager.is_run_stop_requested.return_value = True
        run_control_manager.is_subagent_stop_requested.return_value = False
        run_control_manager.handle_instance_cancelled_async = AsyncMock(
            return_value=True
        )

        harness = TaskControlHarness(
            task_repo=task_repo,
            agent_repo=agent_repo,
            run_runtime_repo=run_runtime_repo,
            event_bus=event_bus,
            run_control_manager=run_control_manager,
        )
        task = _make_task()
        stopped, paused_subagent = await harness.persist_cancelled_execution(
            task,
            "inst_1",
            "role_1",
            is_coordinator=True,
        )
        assert stopped is True
        assert paused_subagent is False


class TestWaitForWorkerWithProgressTimeout:
    @pytest.mark.asyncio
    async def test_worker_completes_before_timeout(self) -> None:
        """Worker finishes quickly; returns True."""
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            message_repo=None,
        )
        task = _make_task()

        async def _quick() -> TaskExecutionResult:
            return TaskExecutionResult(output="quick")

        worker = asyncio.create_task(_quick())
        await asyncio.sleep(0.01)
        result = await harness.wait_for_worker_with_progress_timeout(
            task,
            "inst_1",
            "role_1",
            worker,
            timeout_seconds=5.0,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_timeout_with_no_message_repo(self) -> None:
        """Without message_repo, falls back to simple wait_for."""
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            message_repo=None,
        )
        task = _make_task()

        async def _slow() -> TaskExecutionResult:
            await asyncio.sleep(100)
            return TaskExecutionResult(output="slow")

        worker = asyncio.create_task(_slow())
        result = await harness.wait_for_worker_with_progress_timeout(
            task,
            "inst_1",
            "role_1",
            worker,
            timeout_seconds=0.01,
        )
        assert result is False
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass  # expected: task cancelled after timeout

    @pytest.mark.asyncio
    async def test_with_progress_extends_timeout(self) -> None:
        """When message_repo detects new messages, deadline extends."""
        message_repo = AsyncMock()
        message_repo.get_latest_task_message_id_async = AsyncMock(
            side_effect=[1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
        )
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            message_repo=message_repo,
        )
        task = _make_task()

        async def _slow_with_progress() -> TaskExecutionResult:
            await asyncio.sleep(0.03)
            return TaskExecutionResult(output="slow but progressing")

        worker = asyncio.create_task(_slow_with_progress())
        result = await harness.wait_for_worker_with_progress_timeout(
            task,
            "inst_1",
            "role_1",
            worker,
            timeout_seconds=0.1,
        )
        assert result is True
        assert worker.done()

    @pytest.mark.asyncio
    async def test_no_progress_timeout_expires(self) -> None:
        """When no new messages appear, timeout eventually expires."""
        message_repo = AsyncMock()
        message_repo.get_latest_task_message_id_async = AsyncMock(return_value=0)
        harness = TaskControlHarness(
            task_repo=MagicMock(),
            agent_repo=MagicMock(),
            run_runtime_repo=MagicMock(),
            event_bus=MagicMock(),
            message_repo=message_repo,
        )
        task = _make_task()

        async def _very_slow() -> TaskExecutionResult:
            await asyncio.sleep(100)
            return TaskExecutionResult(output="too slow")

        worker = asyncio.create_task(_very_slow())
        result = await harness.wait_for_worker_with_progress_timeout(
            task,
            "inst_1",
            "role_1",
            worker,
            timeout_seconds=0.01,
        )
        assert result is False
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass  # expected: task cancelled after timeout


class TestTimeoutHandoff:
    def test_no_preexisting_handoff(self) -> None:
        task = _make_task(handoff=None)
        handoff = _ch_mod._timeout_handoff(task=task, timeout_seconds=30.0)
        assert "timeout after 30s" in handoff.reason
        assert len(handoff.incomplete) > 0
        assert len(handoff.next_steps) > 0

    def test_preexisting_handoff_preserves_fields(self) -> None:
        existing = TaskHandoff(reason="custom reason", incomplete=("item1", "item2"))
        task = _make_task(handoff=existing)
        handoff = _ch_mod._timeout_handoff(task=task, timeout_seconds=30.0)
        assert "custom reason" in handoff.reason
        assert handoff.incomplete == ("item1", "item2")
