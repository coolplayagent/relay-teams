# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.reflection.config_manager import ReflectionConfigManager
from agent_teams.reflection.models import DailyReflectionResult, LongTermMemoryDocument
from agent_teams.reflection.repository import ReflectionJobRepository
from agent_teams.reflection.service import (
    ConsolidationPromptInput,
    ReflectionPromptInput,
    ReflectionService,
)
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.workflow_graph_repo import WorkflowGraphRepository
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
    build_workspace_id,
)
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.models import TaskEnvelope, VerificationPlan
from agent_teams.workflow.registry import WorkflowRegistry
from agent_teams.workflow.spec import WorkflowDefinition, WorkflowTaskTemplate


class _CapturingProvider:
    def __init__(self) -> None:
        self.prompts: list[str | None] = []
        self.system_prompts: list[str] = []

    async def generate(self, request: object) -> str:
        prompt = getattr(request, "user_prompt", None)
        system_prompt = getattr(request, "system_prompt", "")
        assert prompt is None or isinstance(prompt, str)
        assert isinstance(system_prompt, str)
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        return "ok"


class _InterruptingProvider:
    async def generate(self, request: object) -> str:
        _ = request
        raise asyncio.CancelledError


def _build_service(
    db_path: Path,
    provider: object,
) -> tuple[
    TaskExecutionService,
    TaskRepository,
    AgentInstanceRepository,
    MessageRepository,
    InstancePool,
]:
    role = RoleDefinition(
        role_id="time",
        name="time",
        version="1",
        tools=(),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    instance_pool = InstancePool()
    shared_store = SharedStateRepository(db_path)

    service = TaskExecutionService(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        workflow_graph_repo=WorkflowGraphRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=lambda _: provider,
    )
    return service, task_repo, agent_repo, message_repo, instance_pool


def _build_service_with_control(
    db_path: Path,
    provider: object,
) -> tuple[
    TaskExecutionService,
    TaskRepository,
    AgentInstanceRepository,
    MessageRepository,
    InstancePool,
    RunRuntimeRepository,
    RunControlManager,
]:
    role = RoleDefinition(
        role_id="time",
        name="time",
        version="1",
        tools=(),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    instance_pool = InstancePool()
    event_log = EventLog(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    run_control_manager = RunControlManager()
    run_control_manager.bind_runtime(
        run_event_hub=RunEventHub(),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
        event_bus=event_log,
        run_runtime_repo=run_runtime_repo,
    )

    service = TaskExecutionService(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        workflow_graph_repo=WorkflowGraphRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=run_runtime_repo,
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=lambda _: provider,
        run_control_manager=run_control_manager,
    )
    return (
        service,
        task_repo,
        agent_repo,
        message_repo,
        instance_pool,
        run_runtime_repo,
        run_control_manager,
    )


def _seed_task(
    *,
    task_repo: TaskRepository,
    agent_repo: AgentInstanceRepository,
    message_repo: MessageRepository,
    instance_pool: InstancePool,
) -> tuple[TaskEnvelope, str]:
    workspace_id = build_workspace_id("session-1")
    conversation_id = build_conversation_id("session-1", "time")
    instance = instance_pool.create_subagent(
        "time",
        workspace_id=workspace_id,
        conversation_id=conversation_id,
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
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=instance.instance_id,
        role_id="time",
        workspace_id=instance.workspace_id,
        conversation_id=instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    message_repo.append(
        session_id="session-1",
        workspace_id=instance.workspace_id,
        conversation_id=instance.conversation_id,
        agent_role_id="time",
        instance_id=instance.instance_id,
        task_id="task-1",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="query time")])],
    )
    return task, instance.instance_id


@pytest.mark.asyncio
async def test_execute_omits_objective_when_task_history_exists(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    service, task_repo, agent_repo, message_repo, instance_pool = _build_service(
        tmp_path / "task_execution_service.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
    )

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert result == "ok"
    assert provider.prompts == [None]


@pytest.mark.asyncio
async def test_execute_persists_objective_before_first_turn(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    service, task_repo, agent_repo, message_repo, instance_pool = _build_service(
        tmp_path / "task_execution_service_objective.db",
        provider,
    )
    workspace_id = build_workspace_id("session-1")
    conversation_id = build_conversation_id("session-1", "time")
    instance = instance_pool.create_subagent(
        "time",
        workspace_id=workspace_id,
        conversation_id=conversation_id,
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
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=instance.instance_id,
        role_id="time",
        workspace_id=instance.workspace_id,
        conversation_id=instance.conversation_id,
        status=InstanceStatus.IDLE,
    )

    result = await service.execute(
        instance_id=instance.instance_id,
        role_id="time",
        task=task,
    )

    assert result == "ok"
    assert provider.prompts == [None]
    history = message_repo.get_history_for_task(instance.instance_id, "task-1")
    assert len(history) == 1
    assert isinstance(history[0], ModelRequest)
    assert history[0].parts[0].content == "query time"


@pytest.mark.asyncio
async def test_execute_persists_followup_prompt_before_turn(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    service, task_repo, agent_repo, message_repo, instance_pool = _build_service(
        tmp_path / "task_execution_service_followup.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
    )

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
        user_prompt_override="Follow up: query time again.",
    )

    assert result == "ok"
    assert provider.prompts == [None]
    history = message_repo.get_history_for_task(instance_id, "task-1")
    assert len(history) == 2
    assert isinstance(history[-1], ModelRequest)
    assert history[-1].parts[0].content == "Follow up: query time again."


@pytest.mark.asyncio
async def test_execute_marks_run_stop_as_stopped_idle_not_paused_followup(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        agent_repo,
        message_repo,
        instance_pool,
        run_runtime_repo,
        run_control_manager,
    ) = _build_service_with_control(
        tmp_path / "task_execution_service_run_stop.db",
        _InterruptingProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
    )
    _ = run_control_manager.request_run_stop("run-1")

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPED
    assert runtime.phase == RunRuntimePhase.IDLE
    assert runtime.active_task_id is None
    assert runtime.active_role_id is None
    assert runtime.active_subagent_instance_id is None
    record = task_repo.get(task.task_id)
    assert record.status == TaskStatus.STOPPED
    assert record.error_message == "Task stopped by user"


@pytest.mark.asyncio
async def test_execute_marks_subagent_stop_as_awaiting_followup(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        agent_repo,
        message_repo,
        instance_pool,
        run_runtime_repo,
        run_control_manager,
    ) = _build_service_with_control(
        tmp_path / "task_execution_service_subagent_stop.db",
        _InterruptingProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
    )
    _ = run_control_manager.request_subagent_stop(
        run_id="run-1",
        instance_id=instance_id,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPED
    assert runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
    assert runtime.active_task_id == task.task_id
    assert runtime.active_role_id == "time"
    assert runtime.active_subagent_instance_id == instance_id
    record = task_repo.get(task.task_id)
    assert record.status == TaskStatus.STOPPED
    assert record.error_message == "Task stopped by user"


@pytest.mark.asyncio
async def test_execute_coordinator_receives_workflow_recommendation(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
            name="Coordinator Agent",
            version="1",
            tools=(),
            system_prompt="Coordinate tasks.",
        )
    )
    workflow_registry = WorkflowRegistry()
    workflow_registry.register(
        WorkflowDefinition(
            workflow_id="sdd",
            name="Standard Delivery Workflow",
            version="1",
            selection_hints=("build", "api", "service"),
            is_default=True,
            tasks=(
                WorkflowTaskTemplate(
                    task_name="spec",
                    role_id="coordinator_agent",
                    objective_template="Plan {objective}",
                ),
            ),
            guidance="Use this workflow for staged software delivery.",
        )
    )
    db_path = tmp_path / "task_execution_service_coordinator.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    instance_pool = InstancePool()
    shared_store = SharedStateRepository(db_path)
    workspace_id = build_workspace_id("session-1")
    conversation_id = build_conversation_id("session-1", "coordinator_agent")
    instance = instance_pool.create_subagent(
        "coordinator_agent",
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="Build an API service",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=instance.instance_id,
        role_id="coordinator_agent",
        workspace_id=instance.workspace_id,
        conversation_id=instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    service = TaskExecutionService(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        workflow_graph_repo=WorkflowGraphRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=lambda _: provider,
        workflow_registry=workflow_registry,
    )

    result = await service.execute(
        instance_id=instance.instance_id,
        role_id="coordinator_agent",
        task=task,
    )

    assert result == "ok"
    assert provider.system_prompts
    assert "## Workflow Recommendation" in provider.system_prompts[0]
    assert (
        "Recommended workflow: sdd (Standard Delivery Workflow)"
        in provider.system_prompts[0]
    )
    assert "Do not derive task order from role metadata." in provider.system_prompts[0]


class _FakeReflectionService:
    def __init__(self, memory_text: str = "") -> None:
        self.memory_text = memory_text
        self.enqueued: list[dict[str, str]] = []

    async def generate_daily_reflection(
        self,
        prompt_input: ReflectionPromptInput,
    ) -> DailyReflectionResult:
        raise AssertionError(f"unexpected daily reflection call: {prompt_input}")

    async def consolidate_long_term_memory(
        self,
        prompt_input: ConsolidationPromptInput,
    ) -> LongTermMemoryDocument:
        raise AssertionError(f"unexpected long-term consolidation call: {prompt_input}")

    def build_injected_memory(
        self,
        *,
        session_id: str,
        role_id: str,
        workspace_id: str,
    ) -> str:
        _ = (session_id, role_id, workspace_id)
        return self.memory_text

    def enqueue_daily_reflection(
        self,
        *,
        session_id: str,
        run_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> None:
        self.enqueued.append(
            {
                "session_id": session_id,
                "run_id": run_id,
                "task_id": task_id,
                "instance_id": instance_id,
                "role_id": role_id,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
            }
        )


@pytest.mark.asyncio
async def test_execute_injects_memory_and_enqueues_reflection(tmp_path: Path) -> None:
    provider = _CapturingProvider()
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_dir = project_root / ".agent_teams"
    config_dir.mkdir()
    role = RoleDefinition(
        role_id="time",
        name="time",
        version="1",
        tools=(),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)
    db_path = tmp_path / "task_execution_service_reflection.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    instance_pool = InstancePool()
    shared_store = SharedStateRepository(db_path)
    reflection_service = ReflectionService(
        config_manager=ReflectionConfigManager(config_dir=config_dir),
        repository=ReflectionJobRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=project_root, shared_store=shared_store
        ),
        message_repo=message_repo,
        task_repo=task_repo,
        agent_repo=agent_repo,
        model_client=_FakeReflectionService(memory_text="- Prefer concise output."),
    )
    reflection_service.build_injected_memory = lambda **_: "- Prefer concise output."
    service = TaskExecutionService(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        workflow_graph_repo=WorkflowGraphRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=project_root, shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=lambda _: provider,
        reflection_service=reflection_service,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
    )

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert result == "ok"
    assert provider.system_prompts
    assert "## Workspace Memory" in provider.system_prompts[0]
    assert "Prefer concise output." in provider.system_prompts[0]
    jobs = reflection_service.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].role_id == "time"
    assert jobs[0].instance_id == instance_id
