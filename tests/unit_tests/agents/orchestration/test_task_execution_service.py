# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.media import (
    MediaAssetRepository,
    MediaAssetService,
    content_parts_from_text,
)
from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.agents.instances.models import create_subagent_instance
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.agents.execution.system_prompts import RuntimePromptBuilder
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.retrieval import RetrievalService, SqliteFts5RetrievalStore
from agent_teams.roles.memory_repository import RoleMemoryRepository
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.skills.skill_routing_service import SkillRuntimeService
from agent_teams.tools.registry import build_default_registry
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
)
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


class _CapturingProvider:
    def __init__(self) -> None:
        self.prompts: list[str | None] = []
        self.system_prompts: list[str] = []
        self.thinking_enabled: list[bool] = []
        self.thinking_efforts: list[str | None] = []

    async def generate(self, request: object) -> str:
        prompt = getattr(request, "user_prompt", None)
        system_prompt = getattr(request, "system_prompt", "")
        thinking = getattr(request, "thinking", None)
        assert prompt is None or isinstance(prompt, str)
        assert isinstance(system_prompt, str)
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        self.thinking_enabled.append(getattr(thinking, "enabled", False) is True)
        self.thinking_efforts.append(getattr(thinking, "effort", None))
        return "ok"


class _InterruptingProvider:
    async def generate(self, request: object) -> str:
        _ = request
        raise asyncio.CancelledError


class _RecoverablePauseProvider:
    async def generate(self, request: object) -> str:
        raise RecoverableRunPauseError(
            RecoverableRunPausePayload(
                run_id=str(getattr(request, "run_id")),
                trace_id=str(getattr(request, "trace_id")),
                task_id=str(getattr(request, "task_id")),
                session_id=str(getattr(request, "session_id")),
                instance_id=str(getattr(request, "instance_id")),
                role_id=str(getattr(request, "role_id")),
                error_code="network_stream_interrupted",
                error_message="stream interrupted",
                retries_used=1,
                total_attempts=3,
            )
        )


def _build_service(
    db_path: Path,
    provider: object,
) -> tuple[
    TaskExecutionService,
    TaskRepository,
    AgentInstanceRepository,
    MessageRepository,
]:
    role = RoleDefinition(
        role_id="time",
        name="time",
        description="Reports the current time.",
        version="1",
        tools=(),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)

    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=SkillRegistry.from_config_dirs(app_config_dir=db_path.parent),
        mcp_registry=McpRegistry(),
        run_intent_repo=RunIntentRepository(db_path),
    )
    return service, task_repo, agent_repo, message_repo


def _write_skill(app_config_dir: Path, *, name: str, description: str) -> None:
    skill_dir = app_config_dir / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{description}\n",
        encoding="utf-8",
    )


def _build_service_with_control(
    db_path: Path,
    provider: object,
) -> tuple[
    TaskExecutionService,
    TaskRepository,
    AgentInstanceRepository,
    MessageRepository,
    RunRuntimeRepository,
    RunControlManager,
]:
    role = RoleDefinition(
        role_id="time",
        name="time",
        description="Reports the current time.",
        version="1",
        tools=(),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
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
        event_bus=event_log,
        run_runtime_repo=run_runtime_repo,
    )

    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=run_runtime_repo,
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=SkillRegistry.from_config_dirs(app_config_dir=db_path.parent),
        mcp_registry=McpRegistry(),
        run_control_manager=run_control_manager,
        run_intent_repo=RunIntentRepository(db_path),
    )
    return (
        service,
        task_repo,
        agent_repo,
        message_repo,
        run_runtime_repo,
        run_control_manager,
    )


def _seed_task(
    *,
    task_repo: TaskRepository,
    agent_repo: AgentInstanceRepository,
    message_repo: MessageRepository,
) -> tuple[TaskEnvelope, str]:
    workspace_id = "default"
    conversation_id = build_conversation_id("session-1", "time")
    instance = create_subagent_instance(
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
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_service.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
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
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_service_objective.db",
        provider,
    )
    workspace_id = "default"
    conversation_id = build_conversation_id("session-1", "time")
    instance = create_subagent_instance(
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
    runtime_record = agent_repo.get_instance(instance.instance_id)
    assert "You are the time role." in runtime_record.runtime_system_prompt
    assert json.loads(runtime_record.runtime_tools_json) == {
        "local_tools": [],
        "skill_tools": [],
        "mcp_tools": [],
    }


@pytest.mark.asyncio
async def test_execute_runtime_snapshot_includes_skill_list_for_ui(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    role = RoleDefinition(
        role_id="time",
        name="time",
        description="Reports the current time.",
        version="1",
        tools=(),
        skills=("time", "missing_skill"),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)

    db_path = tmp_path / "task_execution_service_runtime_skill_list.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    skill_registry = SkillRegistry.from_config_dirs(app_config_dir=db_path.parent)
    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=skill_registry,
        skill_runtime_service=SkillRuntimeService(
            skill_registry=skill_registry,
            retrieval_service=RetrievalService(
                store=SqliteFts5RetrievalStore(db_path),
            ),
        ),
        mcp_registry=McpRegistry(),
        run_intent_repo=RunIntentRepository(db_path),
    )
    workspace_id = "default"
    conversation_id = build_conversation_id("session-1", "time")
    instance = create_subagent_instance(
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
    instance_id = instance.instance_id

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert result == "ok"
    history = message_repo.get_history_for_task(instance_id, "task-1")
    assert len(history) == 1
    assert isinstance(history[0], ModelRequest)
    prompt_content = history[0].parts[0].content
    assert isinstance(prompt_content, str)
    assert prompt_content == "query time"
    assert "missing_skill" not in prompt_content
    runtime_record = agent_repo.get_instance(instance_id)
    assert "## Available Skills" in runtime_record.runtime_system_prompt
    assert "- time:" in runtime_record.runtime_system_prompt
    assert "missing_skill" not in runtime_record.runtime_system_prompt
    tools_snapshot = json.loads(runtime_record.runtime_tools_json)
    assert len(tools_snapshot["skill_tools"]) == 1
    assert tools_snapshot["skill_tools"][0]["name"] == "load_skill"
    assert tools_snapshot["skill_tools"][0]["source"] == "skill"
    assert "absolute file paths" in tools_snapshot["skill_tools"][0]["description"]


@pytest.mark.asyncio
async def test_execute_runtime_prompt_lists_authorized_runtime_tools(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    role = RoleDefinition(
        role_id="reader",
        name="reader",
        description="Reads workspace files.",
        version="1",
        tools=("read",),
        system_prompt="You are the reader role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)

    db_path = tmp_path / "task_execution_service_runtime_tools_prompt.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=SkillRegistry.from_config_dirs(app_config_dir=db_path.parent),
        mcp_registry=McpRegistry(),
        run_intent_repo=RunIntentRepository(db_path),
    )
    instance = create_subagent_instance(
        "reader",
        workspace_id="default",
        conversation_id=build_conversation_id("session-1", "reader"),
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="read a file",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(task)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=instance.instance_id,
        role_id="reader",
        workspace_id=instance.workspace_id,
        conversation_id=instance.conversation_id,
        status=InstanceStatus.IDLE,
    )

    _ = await service.execute(
        instance_id=instance.instance_id,
        role_id="reader",
        task=task,
    )

    runtime_record = agent_repo.get_instance(instance.instance_id)
    assert "## Authorized Runtime Tools" in runtime_record.runtime_system_prompt
    assert "Local Tools: read" in runtime_record.runtime_system_prompt


@pytest.mark.asyncio
async def test_execute_persists_followup_prompt_before_turn(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_service_followup.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
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
async def test_execute_passes_run_thinking_config_to_provider(tmp_path: Path) -> None:
    provider = _CapturingProvider()
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_service_thinking.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    assert service.run_intent_repo is not None
    service.run_intent_repo.upsert(
        run_id=task.trace_id,
        session_id=task.session_id,
        intent=IntentInput(
            session_id=task.session_id,
            input=content_parts_from_text("query time"),
            thinking=RunThinkingConfig(enabled=True, effort="high"),
        ),
    )

    _ = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert provider.thinking_enabled == [True]
    assert provider.thinking_efforts == ["high"]


@pytest.mark.asyncio
async def test_execute_root_intent_input_appends_routed_skill_candidates(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    role = RoleDefinition(
        role_id="time",
        name="time",
        description="Reports the current time.",
        version="1",
        tools=(),
        skills=(
            "time",
            "planner",
            "sql",
            "docs",
            "api",
            "tests",
            "frontend",
            "ops",
            "calendar",
        ),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)
    db_path = tmp_path / "task_execution_service_root_input_routing.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    for name in (
        "time",
        "planner",
        "sql",
        "docs",
        "api",
        "tests",
        "frontend",
        "ops",
        "calendar",
    ):
        _write_skill(
            db_path.parent,
            name=name,
            description=f"{name} helper",
        )
    skill_registry = SkillRegistry.from_config_dirs(app_config_dir=db_path.parent)
    run_intent_repo = RunIntentRepository(db_path)
    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=skill_registry,
        skill_runtime_service=SkillRuntimeService(
            skill_registry=skill_registry,
            retrieval_service=RetrievalService(
                store=SqliteFts5RetrievalStore(db_path),
            ),
        ),
        mcp_registry=McpRegistry(),
        run_intent_repo=run_intent_repo,
        media_asset_service=MediaAssetService(
            repository=MediaAssetRepository(db_path),
            workspace_manager=WorkspaceManager(
                project_root=Path("."), shared_store=shared_store
            ),
        ),
    )
    workspace_id = "default"
    conversation_id = build_conversation_id("session-1", "time")
    instance = create_subagent_instance(
        "time",
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=None,
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
    run_intent_repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("query time"),
        ),
    )

    result = await service.execute(
        instance_id=instance.instance_id,
        role_id="time",
        task=task,
    )

    assert result == "ok"
    history = message_repo.get_history_for_task(instance.instance_id, "task-1")
    assert len(history) == 1
    assert isinstance(history[0], ModelRequest)
    prompt_content = history[0].parts[0].content
    assert isinstance(prompt_content, str)
    assert prompt_content.startswith("query time")
    assert "## Skill Candidates" in prompt_content
    assert "- time:" in prompt_content


@pytest.mark.asyncio
async def test_execute_marks_run_stop_as_stopped_idle_not_paused_followup(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        agent_repo,
        message_repo,
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
async def test_execute_marks_recoverable_pause_as_awaiting_recovery(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        agent_repo,
        message_repo,
        run_runtime_repo,
        _run_control_manager,
    ) = _build_service_with_control(
        tmp_path / "task_execution_service_pause.db",
        _RecoverablePauseProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )

    with pytest.raises(RecoverableRunPauseError):
        await service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == RunRuntimePhase.AWAITING_RECOVERY
    assert runtime.active_task_id == task.task_id
    assert runtime.active_role_id == "time"
    assert runtime.active_instance_id == instance_id
    assert runtime.active_subagent_instance_id == instance_id
    assert runtime.last_error == "stream interrupted"
    record = task_repo.get(task.task_id)
    assert record.status == TaskStatus.STOPPED
    assert record.error_message == "stream interrupted"


@pytest.mark.asyncio
async def test_execute_coordinator_receives_task_runtime_contract(
    tmp_path: Path,
) -> None:
    provider = _CapturingProvider()
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator Agent",
            description="Coordinates delegated work.",
            version="1",
            tools=("create_tasks", "update_task", "dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer Agent",
            description="Writes implementation changes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=(),
            skills=(),
            system_prompt="Write tasks.",
        )
    )
    db_path = tmp_path / "task_execution_service_coordinator.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    workspace_id = "default"
    conversation_id = build_conversation_id("session-1", "Coordinator")
    instance = create_subagent_instance(
        "Coordinator",
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
        role_id="Coordinator",
        workspace_id=instance.workspace_id,
        conversation_id=instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=SkillRegistry.from_config_dirs(app_config_dir=db_path.parent),
        mcp_registry=McpRegistry(),
    )

    result = await service.execute(
        instance_id=instance.instance_id,
        role_id="Coordinator",
        task=task,
    )

    assert result == "ok"
    assert provider.system_prompts
    assert "Coordinate tasks." in provider.system_prompts[0]
    assert "## Orchestration Rules" in provider.system_prompts[0]
    assert "## Available Roles" in provider.system_prompts[0]
    assert (
        "Delegate only when another role is a better fit than continuing yourself."
        in provider.system_prompts[0]
    )
    assert (
        "If no existing role is a good fit, create a run-scoped role with `create_temporary_role` before dispatch."
        in provider.system_prompts[0]
    )
    assert (
        "The roles listed below are dispatch targets, not your own capabilities."
        in provider.system_prompts[0]
    )
    assert "### writer_agent" in provider.system_prompts[0]
    assert "- Source: static" in provider.system_prompts[0]


@pytest.mark.asyncio
async def test_build_runtime_tools_snapshot_uses_external_tool_descriptions(
    tmp_path: Path,
) -> None:
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator Agent",
            description="Coordinates delegated work.",
            version="1",
            tools=("create_tasks", "update_task", "dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="writer_agent",
            name="Writer Agent",
            description="Writes implementation changes.",
            version="1",
            tools=("read", "write"),
            mcp_servers=(),
            skills=(),
            system_prompt="Write tasks.",
        )
    )
    db_path = tmp_path / "task_execution_service_snapshot.db"
    shared_store = SharedStateRepository(db_path)
    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=TaskRepository(db_path),
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=Path("."), shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: _CapturingProvider(),
        tool_registry=build_default_registry(),
        skill_registry=SkillRegistry.from_config_dirs(app_config_dir=db_path.parent),
        mcp_registry=McpRegistry(),
    )

    coordinator_snapshot = await service._build_runtime_tools_snapshot(
        role_registry.get("Coordinator")
    )
    writer_snapshot = await service._build_runtime_tools_snapshot(
        role_registry.get("writer_agent")
    )

    coordinator_tools = {
        entry.name: entry.description for entry in coordinator_snapshot.local_tools
    }
    writer_tools = {
        entry.name: entry.description for entry in writer_snapshot.local_tools
    }

    assert coordinator_tools["create_tasks"].startswith(
        "Create one or more run-scoped delegated task contracts."
    )
    assert writer_tools["read"].startswith("Read a file or directory from disk.")
    assert writer_tools["write"].startswith(
        "Write full file contents to the workspace."
    )


@pytest.mark.asyncio
async def test_execute_injects_memory_and_records_role_memory(tmp_path: Path) -> None:
    provider = _CapturingProvider()
    project_root = tmp_path / "project"
    project_root.mkdir()
    role = RoleDefinition(
        role_id="time",
        name="time",
        description="Reports the current time.",
        version="1",
        tools=(),
        system_prompt="You are the time role.",
    )
    role_registry = RoleRegistry()
    role_registry.register(role)
    db_path = tmp_path / "task_execution_service_role_memory.db"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    role_memory_service = RoleMemoryService(repository=RoleMemoryRepository(db_path))
    role_memory_service.record_task_result(
        role_id="time",
        workspace_id="default",
        session_id="seed-session",
        task_id="seed-task",
        objective="Be concise",
        result="Prefer concise output.",
        transcript_lines=(),
    )
    role_memory_service.record_task_result(
        role_id="time",
        workspace_id="other-workspace",
        session_id="other-session",
        task_id="other-task",
        objective="Use other cwd",
        result="Refer to /d/workspace/aider.",
        transcript_lines=(),
    )
    service = TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=EventLog(db_path),
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        workspace_manager=WorkspaceManager(
            project_root=project_root, shared_store=shared_store
        ),
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: provider,
        tool_registry=build_default_registry(),
        skill_registry=SkillRegistry.from_config_dirs(app_config_dir=db_path.parent),
        mcp_registry=McpRegistry(),
        role_memory_service=role_memory_service,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert result == "ok"
    assert provider.system_prompts
    assert "## Reflection Memory" in provider.system_prompts[0]
    assert "Prefer concise output." in provider.system_prompts[0]
    assert "/d/workspace/aider" not in provider.system_prompts[0]
    durable = role_memory_service.build_injected_memory(
        role_id="time",
        workspace_id="default",
    )
    assert "Prefer concise output." in durable
    assert "query time: ok" not in durable
    assert "Refer to /d/workspace/aider." not in durable
