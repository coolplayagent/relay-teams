import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import UserPromptPart

from relay_teams.agents.instances.enums import InstanceLifecycle, InstanceStatus
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


def test_request_run_stop_cancels_run_task() -> None:
    async def _case() -> None:
        mgr = RunControlManager()

        async def _worker() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(_worker())
        mgr.register_run_task(run_id="run-1", session_id="session-1", task=task)
        assert mgr.request_run_stop("run-1") is True
        await asyncio.sleep(0)
        assert task.cancelled()
        assert mgr.is_run_stop_requested("run-1") is True

    asyncio.run(_case())


def test_request_subagent_stop_marks_paused_context() -> None:
    async def _case() -> None:
        mgr = RunControlManager()

        async def _subagent() -> str:
            await asyncio.sleep(10)
            return "x"

        task = asyncio.create_task(_subagent())
        mgr.register_instance_task(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="generalist",
            task_id="task-1",
            task=task,
        )

        paused = mgr.request_subagent_stop(run_id="run-1", instance_id="inst-1")
        assert paused is not None
        assert paused.session_id == "session-1"
        assert paused.instance_id == "inst-1"
        assert paused.task_id == "task-1"
        await asyncio.sleep(0)
        assert task.cancelled()
        assert (
            mgr.is_subagent_stop_requested(run_id="run-1", instance_id="inst-1") is True
        )
        assert (
            mgr.is_subagent_paused(session_id="session-1", instance_id="inst-1") is True
        )

    asyncio.run(_case())


def test_request_subagent_stop_tracks_multiple_pauses_per_session() -> None:
    async def _case() -> None:
        mgr = RunControlManager()

        async def _subagent() -> str:
            await asyncio.sleep(10)
            return "x"

        first_task = asyncio.create_task(_subagent())
        second_task = asyncio.create_task(_subagent())
        mgr.register_instance_task(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="generalist",
            task_id="task-1",
            task=first_task,
        )
        mgr.register_instance_task(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-2",
            role_id="reviewer",
            task_id="task-2",
            task=second_task,
        )

        first_paused = mgr.request_subagent_stop(run_id="run-1", instance_id="inst-1")
        second_paused = mgr.request_subagent_stop(run_id="run-1", instance_id="inst-2")
        await asyncio.sleep(0)

        assert first_paused is not None
        assert second_paused is not None
        assert first_task.cancelled()
        assert second_task.cancelled()
        assert (
            mgr.is_subagent_paused(session_id="session-1", instance_id="inst-1") is True
        )
        assert (
            mgr.is_subagent_paused(session_id="session-1", instance_id="inst-2") is True
        )
        assert (
            mgr.release_paused_subagent(session_id="session-1", instance_id="missing")
            is None
        )

        released = mgr.release_paused_subagent(
            session_id="session-1", instance_id="inst-1"
        )

        assert released is not None
        assert released.instance_id == "inst-1"
        assert (
            mgr.is_subagent_stop_requested(run_id="run-1", instance_id="inst-1")
            is False
        )
        assert (
            mgr.is_subagent_stop_requested(run_id="run-1", instance_id="inst-2") is True
        )
        assert (
            mgr.is_subagent_paused(session_id="session-1", instance_id="inst-1")
            is False
        )
        assert (
            mgr.is_subagent_paused(session_id="session-1", instance_id="inst-2") is True
        )
        latest_released = mgr.release_paused_subagent(session_id="session-1")
        assert latest_released is not None
        assert latest_released.instance_id == "inst-2"
        assert (
            mgr.is_subagent_stop_requested(run_id="run-1", instance_id="inst-2")
            is False
        )
        assert mgr.get_paused_subagent("session-1") is None
        await asyncio.gather(first_task, second_task, return_exceptions=True)

    asyncio.run(_case())


def test_clear_paused_subagent_for_run_keeps_other_run_pauses() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="generalist",
        task_id="task-1",
    )
    mgr.pause_subagent(
        session_id="session-1",
        run_id="run-2",
        instance_id="inst-2",
        role_id="reviewer",
        task_id="task-2",
    )

    mgr.clear_paused_subagent_for_run("run-1")

    assert mgr.is_subagent_paused(session_id="session-1", instance_id="inst-1") is False
    assert mgr.is_subagent_paused(session_id="session-1", instance_id="inst-2") is True

    mgr.clear_paused_subagent_for_run("run-2")

    assert mgr.get_paused_subagent("session-1") is None


def test_release_paused_subagent_clears_blocking() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="generalist",
        task_id="task-1",
    )
    released = mgr.release_paused_subagent(session_id="session-1", instance_id="inst-1")
    assert released is not None
    assert mgr.get_paused_subagent("session-1") is None
    assert mgr.is_subagent_stop_requested(run_id="run-1", instance_id="inst-1") is False


def test_get_coordinator_instance_id_uses_assigned_ephemeral_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_ephemeral_root.db"
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    mgr = RunControlManager()
    mgr.bind_runtime(
        run_event_hub=RunEventHub(),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=MessageRepository(db_path),
        event_bus=EventLog(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
    )
    child_task = TaskEnvelope(
        task_id="task-child",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="worker",
        objective="child work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    root_task = TaskEnvelope(
        task_id="task-root",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="handle follow-up",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(child_task)
    _ = task_repo.create(root_task)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-ephemeral-root",
        role_id="Coordinator",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.RUNNING,
        lifecycle=InstanceLifecycle.EPHEMERAL,
    )
    task_repo.update_status(
        root_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id="inst-ephemeral-root",
    )

    assert (
        mgr.get_coordinator_instance_id(run_id="run-1", session_id="session-1")
        == "inst-ephemeral-root"
    )
    assert agent_repo.get_session_role_instance_id("session-1", "Coordinator") is None


@pytest.mark.asyncio
async def test_inject_to_running_agents_async_publishes_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_async_injection.db"
    event_log = EventLog(db_path)
    injection_manager = RunInjectionManager()
    injection_manager.activate("run-1")
    agent_repo = AgentInstanceRepository(db_path)
    mgr = RunControlManager()
    mgr.bind_runtime(
        run_event_hub=RunEventHub(
            event_log=event_log,
            run_state_repo=RunStateRepository(db_path),
        ),
        injection_manager=injection_manager,
        agent_repo=agent_repo,
        task_repo=TaskRepository(db_path),
        message_repo=MessageRepository(db_path),
        event_bus=event_log,
        run_runtime_repo=RunRuntimeRepository(db_path),
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="worker",
        workspace_id="workspace-1",
        status=InstanceStatus.RUNNING,
    )

    message = await mgr.inject_to_running_agents_async(
        run_id="run-1",
        source=InjectionSource.USER,
        content="continue",
    )

    assert message.recipient_instance_id == "inst-1"
    queued = injection_manager.drain_at_boundary("run-1", "inst-1")
    assert len(queued) == 1
    events = await event_log.list_by_session_with_ids_async("session-1")
    assert events[-1]["event_type"] == RunEventType.INJECTION_ENQUEUED.value


@pytest.mark.asyncio
async def test_handle_instance_cancelled_async_persists_stop_without_sync_write(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_async_cancel.db"
    event_log = EventLog(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    mgr = RunControlManager()
    mgr.bind_runtime(
        run_event_hub=RunEventHub(),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=MessageRepository(db_path),
        event_bus=event_log,
        run_runtime_repo=RunRuntimeRepository(db_path),
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="MainAgent",
        objective="cancel me",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = await task_repo.create_async(task)
    await agent_repo.upsert_instance_async(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="MainAgent",
        workspace_id="workspace-1",
        status=InstanceStatus.RUNNING,
    )

    async def _worker() -> None:
        await asyncio.sleep(10)

    run_task = asyncio.create_task(_worker())
    mgr.register_run_task(run_id="run-1", session_id="session-1", task=run_task)
    assert mgr.request_run_stop("run-1") is True
    stopped = await mgr.handle_instance_cancelled_async(
        task=task,
        instance_id="inst-1",
    )
    await asyncio.gather(run_task, return_exceptions=True)

    assert stopped is True
    assert (await task_repo.get_async("task-1")).status == TaskStatus.STOPPED
    assert (
        await agent_repo.get_instance_async("inst-1")
    ).status == InstanceStatus.STOPPED
    events = await event_log.list_by_session_async("session-1")
    assert [event["event_type"] for event in events] == [
        "task_stopped",
        "instance_stopped",
    ]


def test_context_raises_when_cancelled() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="generalist",
        task_id="task-1",
    )
    ctx = mgr.context(run_id="run-1", instance_id="inst-1")
    with pytest.raises(asyncio.CancelledError):
        ctx.raise_if_cancelled()


def test_run_stop_flag_survives_run_task_unregister_until_resume() -> None:
    async def _case() -> None:
        mgr = RunControlManager()

        async def _worker() -> None:
            await asyncio.sleep(10)

        async def _subagent() -> str:
            await asyncio.sleep(10)
            return "x"

        run_task = asyncio.create_task(_worker())
        inst_task = asyncio.create_task(_subagent())
        mgr.register_run_task(run_id="run-1", session_id="session-1", task=run_task)
        mgr.register_instance_task(
            run_id="run-1",
            session_id="session-1",
            instance_id="inst-1",
            role_id="generalist",
            task_id="task-1",
            task=inst_task,
        )

        assert mgr.request_run_stop("run-1") is True
        mgr.unregister_run_task("run-1")
        assert mgr.is_cancelled(run_id="run-1", instance_id="inst-1") is True

        resumed_task = asyncio.create_task(_worker())
        mgr.register_run_task(run_id="run-1", session_id="session-1", task=resumed_task)
        assert mgr.is_run_stop_requested("run-1") is False

        resumed_task.cancel()
        inst_task.cancel()
        await asyncio.gather(resumed_task, inst_task, return_exceptions=True)

    asyncio.run(_case())


def test_session_guard_blocks_main_input_when_paused() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="generalist",
        task_id="task-1",
    )
    with pytest.raises(RuntimeError):
        mgr.assert_session_allows_main_input("session-1")


def test_session_guard_uses_runtime_fallback_when_process_restarted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_runtime_fallback.db"
    mgr = RunControlManager()
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    mgr.bind_runtime(
        run_event_hub=RunEventHub(
            event_log=event_log,
            run_state_repo=RunStateRepository(db_path),
        ),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=TaskRepository(db_path),
        message_repo=MessageRepository(db_path),
        event_bus=event_log,
        run_runtime_repo=run_runtime_repo,
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        workspace_id="default",
        status=InstanceStatus.STOPPED,
    )
    run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
    )
    run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
        active_instance_id="inst-1",
        active_task_id="task-1",
        active_role_id="time",
        active_subagent_instance_id="inst-1",
        last_error="Subagent stopped by user",
    )

    paused = mgr.get_paused_subagent("session-1")

    assert paused is not None
    assert paused.instance_id == "inst-1"
    assert paused.role_id == "time"
    with pytest.raises(RuntimeError):
        mgr.assert_session_allows_main_input("session-1")


def test_session_guard_recovers_each_paused_subagent_from_stopped_tasks(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_stopped_task_fallback.db"
    mgr = RunControlManager()
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    mgr.bind_runtime(
        run_event_hub=RunEventHub(
            event_log=event_log,
            run_state_repo=RunStateRepository(db_path),
        ),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=MessageRepository(db_path),
        event_bus=event_log,
        run_runtime_repo=run_runtime_repo,
    )
    first_task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="generalist",
        objective="draft section",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    second_task = TaskEnvelope(
        task_id="task-2",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="reviewer",
        objective="review section",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(first_task)
    _ = task_repo.create(second_task)
    task_repo.update_status(
        "task-1",
        TaskStatus.STOPPED,
        assigned_instance_id="inst-1",
        error_message="Task stopped by user",
    )
    task_repo.update_status(
        "task-2",
        TaskStatus.STOPPED,
        assigned_instance_id="inst-2",
        error_message="Task stopped by user",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="generalist",
        workspace_id="default",
        status=InstanceStatus.STOPPED,
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-2",
        role_id="reviewer",
        workspace_id="default",
        status=InstanceStatus.STOPPED,
    )
    run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.SUBAGENT_RUNNING,
    )
    run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.SUBAGENT_RUNNING,
        active_instance_id="inst-running",
        active_task_id="task-running",
        active_role_id="writer",
        active_subagent_instance_id="inst-running",
    )

    first_paused = mgr.get_paused_subagent("session-1", instance_id="inst-1")
    second_paused = mgr.get_paused_subagent("session-1", instance_id="inst-2")

    assert first_paused is not None
    assert first_paused.task_id == "task-1"
    assert first_paused.role_id == "generalist"
    assert second_paused is not None
    assert second_paused.task_id == "task-2"
    assert second_paused.role_id == "reviewer"
    assert mgr.is_subagent_paused(session_id="session-1", instance_id="inst-1") is True
    assert mgr.is_subagent_paused(session_id="session-1", instance_id="inst-2") is True
    with pytest.raises(RuntimeError, match="Subagent"):
        mgr.assert_session_allows_main_input("session-1")


def test_resume_subagent_with_message_uses_same_instance_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_resume_subagent.db"
    mgr = RunControlManager()
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    mgr.bind_runtime(
        run_event_hub=RunEventHub(
            event_log=event_log,
            run_state_repo=RunStateRepository(db_path),
        ),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=message_repo,
        event_bus=event_log,
        run_runtime_repo=run_runtime_repo,
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        "task-1",
        TaskStatus.STOPPED,
        assigned_instance_id="inst-1",
        error_message="Task stopped by user",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        workspace_id="default",
        status=InstanceStatus.STOPPED,
    )
    message_repo.append_user_prompt_if_missing(
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="query time",
    )
    run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
    )
    run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
        active_instance_id="inst-1",
        active_task_id="task-1",
        active_role_id="time",
        active_subagent_instance_id="inst-1",
        last_error="Subagent stopped by user",
    )

    mgr.resume_subagent_with_message(
        run_id="run-1",
        instance_id="inst-1",
        content="query time again",
    )

    history = message_repo.get_history_for_task("inst-1", "task-1")
    assert len(history) == 2
    assert isinstance(history[-1].parts[0], UserPromptPart)
    assert history[-1].parts[0].content == "query time again"
    task_record = task_repo.get("task-1")
    assert task_record.status == TaskStatus.ASSIGNED
    assert task_record.assigned_instance_id == "inst-1"
    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.phase == RunRuntimePhase.SUBAGENT_RUNNING
    assert runtime.active_subagent_instance_id == "inst-1"


def test_resume_subagent_with_message_blocks_unpaused_target_when_other_paused(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_control_resume_other_paused.db"
    mgr = RunControlManager()
    agent_repo = AgentInstanceRepository(db_path)
    mgr.bind_runtime(
        run_event_hub=RunEventHub(),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=TaskRepository(db_path),
        message_repo=MessageRepository(db_path),
        event_bus=EventLog(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-2",
        role_id="reviewer",
        workspace_id="default",
        status=InstanceStatus.IDLE,
    )
    mgr.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="generalist",
        task_id="task-1",
    )

    with pytest.raises(RuntimeError, match="inst-1"):
        mgr.resume_subagent_with_message(
            run_id="run-1",
            instance_id="inst-2",
            content="continue",
        )
