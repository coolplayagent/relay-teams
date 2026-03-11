from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.agents.enums import InstanceStatus
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.runtime import ToolContext
from agent_teams.tools.workflow_tools.dispatch_tasks import (
    _converged_stage,
    _dispatch_next,
    _dispatch_revise,
    _latest_completed_task,
    _next_action,
    _progress,
)
from agent_teams.workspace import (
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_workspace_id,
    build_workspace_id,
)
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.models import TaskEnvelope, TaskRecord, VerificationPlan
from agent_teams.workflow.runtime_graph import get_ready_tasks
from agent_teams.workflow.status_snapshot import build_task_status_snapshot


def _record(task_id: str, status: TaskStatus) -> TaskRecord:
    envelope = TaskEnvelope(
        task_id=task_id,
        session_id="session-1",
        parent_task_id="root-task",
        trace_id="run-1",
        objective="demo",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    return TaskRecord(
        envelope=envelope,
        status=status,
        assigned_instance_id="inst-1",
    )


def test_latest_completed_task_prefers_most_recent_stage_order() -> None:
    tasks = {
        "spec": {"task_id": "task-spec"},
        "design": {"task_id": "task-design"},
        "code": {"task_id": "task-code"},
    }
    records = {
        "task-spec": _record("task-spec", TaskStatus.COMPLETED),
        "task-design": _record("task-design", TaskStatus.COMPLETED),
        "task-code": _record("task-code", TaskStatus.CREATED),
    }
    latest = _latest_completed_task(tasks=tasks, records=records)
    assert latest == ("design", "task-design")


def test_converged_stage_and_next_action() -> None:
    tasks = {
        "spec": {"task_id": "task-spec"},
        "design": {"task_id": "task-design"},
    }
    records = {
        "task-spec": _record("task-spec", TaskStatus.COMPLETED),
        "task-design": _record("task-design", TaskStatus.CREATED),
    }
    progress = _progress(tasks=tasks, records=records)
    assert progress == {"completed": 1, "total": 2}

    stage = _converged_stage(progress=progress, failed=[])
    assert stage == "progress_1_2"
    assert _next_action(stage, failed=[]) == "next"

    failed: list[dict[str, str]] = [{"task_id": "task-design"}]
    failed_stage = _converged_stage(progress=progress, failed=failed)
    assert failed_stage == "failed"
    assert _next_action(failed_stage, failed=failed) == "revise"


def test_task_status_snapshot_includes_result_and_error() -> None:
    tasks = {
        "time_first": {"task_id": "task-time-1", "role_id": "time"},
        "time_second": {"task_id": "task-time-2", "role_id": "time"},
    }
    completed = _record("task-time-1", TaskStatus.COMPLETED)
    completed.result = "2026-03-06 23:13:12"
    failed = _record("task-time-2", TaskStatus.FAILED)
    failed.error_message = "tool timeout"
    failed.assigned_instance_id = "inst-2"

    snapshot = build_task_status_snapshot(
        tasks=tasks,
        records={
            "task-time-1": completed,
            "task-time-2": failed,
        },
    )

    assert snapshot["time_first"]["status"] == "completed"
    assert snapshot["time_first"]["result"] == "2026-03-06 23:13:12"
    assert snapshot["time_first"]["instance_id"] == "inst-1"
    assert snapshot["time_second"]["status"] == "failed"
    assert snapshot["time_second"]["error"] == "tool timeout"
    assert snapshot["time_second"]["instance_id"] == "inst-2"


def test_task_status_snapshot_hides_stale_error_on_completed_task() -> None:
    tasks = {
        "time_first": {"task_id": "task-time-1", "role_id": "time"},
    }
    completed = _record("task-time-1", TaskStatus.COMPLETED)
    completed.result = "2026-03-07 00:41:29"
    completed.error_message = "Task stopped by user"

    snapshot = build_task_status_snapshot(
        tasks=tasks,
        records={"task-time-1": completed},
    )

    assert snapshot["time_first"]["status"] == "completed"
    assert snapshot["time_first"]["result"] == "2026-03-07 00:41:29"
    assert "error" not in snapshot["time_first"]


def test_get_ready_tasks_allows_stopped_task_to_rerun() -> None:
    graph: dict[str, object] = {
        "tasks": {
            "time_first": {
                "task_id": "task-time-1",
                "role_id": "time",
                "depends_on": [],
            }
        }
    }
    ready = get_ready_tasks(
        graph,
        {
            "task-time-1": _record("task-time-1", TaskStatus.STOPPED),
        },
    )

    assert ready == [
        (
            "time_first",
            {
                "task_id": "task-time-1",
                "role_id": "time",
                "depends_on": [],
            },
        )
    ]


class _FakeTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self.task_repo = task_repo
        self.executed_task_ids: list[str] = []
        self.prompts: list[str | None] = []

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str:
        _ = (instance_id, role_id)
        self.executed_task_ids.append(task.task_id)
        self.prompts.append(user_prompt_override)
        result = (
            user_prompt_override
            if user_prompt_override is not None
            else f"completed:{task.task_id}"
        )
        self.task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return result


class _FakeEventLog:
    def list_by_trace(self, trace_id: str) -> list[object]:
        _ = trace_id
        return []


class _FakeDeps:
    def __init__(self, db_path: Path, task_repo: TaskRepository) -> None:
        self.task_repo = task_repo
        self.shared_store = SharedStateRepository(db_path)
        self.instance_pool = InstancePool()
        self.agent_repo = AgentInstanceRepository(db_path)
        self.message_repo = MessageRepository(db_path)
        self.event_bus = _FakeEventLog()
        self.task_execution_service = _FakeTaskExecutionService(task_repo)
        self.run_id = "run-1"
        self.trace_id = "run-1"
        self.task_id = "task-root"
        self.session_id = "session-1"
        self.instance_id = "coord-inst"
        self.role_id = "coordinator_agent"

    def seed_agent(self, *, instance_id: str, role_id: str) -> None:
        self.agent_repo.upsert_instance(
            run_id=self.run_id,
            trace_id=self.trace_id,
            session_id=self.session_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=build_workspace_id(self.session_id),
            conversation_id=build_conversation_id(self.session_id, role_id),
            status=InstanceStatus.IDLE,
        )


class _FakeCtx:
    def __init__(self, deps: _FakeDeps) -> None:
        self.deps = deps
        self.tool_call_id = "dispatch_tasks:2"


@pytest.mark.asyncio
async def test_dispatch_next_advances_from_durable_task_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_resume.db"
    task_repo = TaskRepository(db_path)
    deps = _FakeDeps(db_path, task_repo)
    ctx = _FakeCtx(deps)

    first = TaskEnvelope(
        task_id="task-first",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="first",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    second = TaskEnvelope(
        task_id="task-second",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="second",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(first)
    _ = task_repo.create(second)

    graph: dict[str, object] = {
        "workflow_id": "workflow-1",
        "tasks": {
            "first": {
                "task_id": "task-first",
                "role_id": "time",
                "depends_on": [],
            },
            "second": {
                "task_id": "task-second",
                "role_id": "time",
                "depends_on": [],
            },
        },
    }

    first_result = await _dispatch_next(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="",
        max_dispatch=1,
    )
    second_result = await _dispatch_next(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="",
        max_dispatch=1,
    )

    first_snapshot = cast(dict[str, object], first_result["task_status"])
    second_snapshot = cast(dict[str, object], second_result["task_status"])
    assert cast(dict[str, object], first_snapshot["first"])["status"] == "completed"
    assert cast(dict[str, object], first_snapshot["second"])["status"] == "created"
    assert cast(dict[str, object], second_snapshot["first"])["status"] == "completed"
    assert cast(dict[str, object], second_snapshot["second"])["status"] == "completed"
    assert deps.task_execution_service.executed_task_ids == [
        "task-first",
        "task-second",
    ]


@pytest.mark.asyncio
async def test_dispatch_next_selects_next_ready_task_for_new_tool_call(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_legacy_resume.db"
    task_repo = TaskRepository(db_path)
    deps = _FakeDeps(db_path, task_repo)
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "dispatch_tasks:3"

    first = TaskEnvelope(
        task_id="task-first",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="first",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    second = TaskEnvelope(
        task_id="task-second",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="second",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(first)
    _ = task_repo.create(second)
    task_repo.update_status(
        "task-first",
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-first",
        result="completed:task-first",
    )

    graph: dict[str, object] = {
        "workflow_id": "workflow-1",
        "tasks": {
            "first": {
                "task_id": "task-first",
                "role_id": "time",
                "depends_on": [],
            },
            "second": {
                "task_id": "task-second",
                "role_id": "time",
                "depends_on": ["first"],
            },
        },
    }

    result = await _dispatch_next(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="",
        max_dispatch=1,
    )

    result_snapshot = cast(dict[str, object], result["task_status"])
    assert cast(dict[str, object], result_snapshot["first"])["status"] == "completed"
    assert cast(dict[str, object], result_snapshot["second"])["status"] == "completed"
    dispatched = cast(list[dict[str, object]], result["dispatched"])
    assert len(dispatched) == 1
    assert dispatched[0]["task_id"] == "task-second"
    assert dispatched[0]["task_name"] == "second"
    assert dispatched[0]["role_id"] == "time"
    assert dispatched[0]["instance_id"]
    assert deps.task_execution_service.executed_task_ids == ["task-second"]


@pytest.mark.asyncio
async def test_dispatch_revise_runs_new_followup_turn_for_completed_task(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_revise_followup.db"
    task_repo = TaskRepository(db_path)
    deps = _FakeDeps(db_path, task_repo)
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "dispatch_tasks:4"

    task = TaskEnvelope(
        task_id="task-time",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        "task-time",
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-time",
        result="first-result",
    )
    deps.seed_agent(instance_id="inst-time", role_id="time")
    deps.message_repo.append(
        session_id="session-1",
        workspace_id=build_workspace_id("session-1"),
        conversation_id=build_conversation_id("session-1", "time"),
        agent_role_id="time",
        instance_id="inst-time",
        task_id="task-time",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="query time")])],
    )

    graph: dict[str, object] = {
        "workflow_id": "workflow-1",
        "tasks": {
            "query_time": {
                "task_id": "task-time",
                "role_id": "time",
                "depends_on": [],
            },
        },
    }

    result = await _dispatch_revise(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="query the current time again and compute the delta",
    )

    assert result["ok"] is True
    assert deps.task_execution_service.executed_task_ids == ["task-time"]
    assert deps.task_execution_service.prompts == [None]
    task_row = cast(dict[str, object], result["task"])
    assert task_row["result"] == "completed:task-time"
    history = deps.message_repo.get_history_for_task("inst-time", "task-time")
    assert isinstance(history[-1], ModelRequest)
    assert (
        history[-1].parts[0].content
        == "query the current time again and compute the delta"
    )


@pytest.mark.asyncio
async def test_dispatch_revise_resume_does_not_repeat_followup_prompt_if_history_has_it(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_revise_resume.db"
    task_repo = TaskRepository(db_path)
    deps = _FakeDeps(db_path, task_repo)
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "dispatch_tasks:5"

    task = TaskEnvelope(
        task_id="task-time",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        "task-time",
        TaskStatus.RUNNING,
        assigned_instance_id="inst-time",
    )
    deps.seed_agent(instance_id="inst-time", role_id="time")
    deps.message_repo.append(
        session_id="session-1",
        workspace_id=build_workspace_id("session-1"),
        conversation_id=build_conversation_id("session-1", "time"),
        agent_role_id="time",
        instance_id="inst-time",
        task_id="task-time",
        trace_id="run-1",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="query the current time again")])
        ],
    )

    graph: dict[str, object] = {
        "workflow_id": "workflow-1",
        "tasks": {
            "query_time": {
                "task_id": "task-time",
                "role_id": "time",
                "depends_on": [],
            },
        },
    }

    result = await _dispatch_revise(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="query the current time again",
    )

    assert result["ok"] is True
    assert deps.task_execution_service.prompts == [None]


@pytest.mark.asyncio
async def test_dispatch_revise_requires_feedback(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_revise_requires_feedback.db"
    task_repo = TaskRepository(db_path)
    deps = _FakeDeps(db_path, task_repo)
    ctx = _FakeCtx(deps)

    task = TaskEnvelope(
        task_id="task-time",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    task_repo.update_status(
        "task-time",
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-time",
        result="first-result",
    )
    deps.seed_agent(instance_id="inst-time", role_id="time")

    graph: dict[str, object] = {
        "workflow_id": "workflow-1",
        "tasks": {
            "query_time": {
                "task_id": "task-time",
                "role_id": "time",
                "depends_on": [],
            },
        },
    }

    result = await _dispatch_revise(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="",
    )

    assert result["ok"] is False
    assert result["message"] == "feedback is required for revise."


@pytest.mark.asyncio
async def test_dispatch_next_creates_instance_scoped_workspace_for_subagent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dispatch_instance_workspace.db"
    task_repo = TaskRepository(db_path)
    deps = _FakeDeps(db_path, task_repo)
    ctx = _FakeCtx(deps)

    task = TaskEnvelope(
        task_id="task-time",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)

    graph: dict[str, object] = {
        "workflow_id": "workflow-1",
        "tasks": {
            "query_time": {
                "task_id": "task-time",
                "role_id": "time",
                "depends_on": [],
            },
        },
    }

    result = await _dispatch_next(
        ctx=cast(ToolContext, cast(object, ctx)),
        workflow_id="workflow-1",
        graph=graph,
        feedback="",
        max_dispatch=1,
    )

    dispatched = cast(list[dict[str, object]], result["dispatched"])
    assert len(dispatched) == 1
    instance_id = str(dispatched[0]["instance_id"])
    record = deps.agent_repo.get_instance(instance_id)

    assert record.workspace_id == build_instance_workspace_id(
        "session-1",
        "time",
        instance_id,
    )
    assert record.conversation_id == build_instance_conversation_id(
        "session-1",
        "time",
        instance_id,
    )
    assert record.workspace_id != build_workspace_id("session-1")
    assert record.conversation_id != build_conversation_id("session-1", "time")
