# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from relay_teams.agents.execution.prompt_instructions import PromptInstructionResolver
from relay_teams.agents.execution.system_prompts import RuntimePromptBuilder
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.media import MediaAssetService
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.providers.provider_contracts import LLMProvider
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders import SystemReminderService
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.skills.skill_routing_service import SkillRuntimeService
from relay_teams.tools.registry import ToolRegistry
from relay_teams.workspace import WorkspaceManager
from relay_teams.hooks import HookService


def create_task_execution_service(
    *,
    role_registry: RoleRegistry,
    task_repo: TaskRepository,
    shared_store: SharedStateRepository,
    event_log: EventLog,
    agent_repo: AgentInstanceRepository,
    message_repo: MessageRepository,
    approval_ticket_repo: ApprovalTicketRepository,
    run_runtime_repo: RunRuntimeRepository,
    run_event_hub: RunEventHub | None,
    run_intent_repo: RunIntentRepository,
    workspace_manager: WorkspaceManager,
    media_asset_service: MediaAssetService | None,
    app_config_dir: Path | None,
    prompt_instructions: tuple[str, ...] = (),
    provider_factory: Callable[[RoleDefinition, str | None], LLMProvider],
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
    skill_runtime_service: SkillRuntimeService | None,
    mcp_registry: McpRegistry,
    injection_manager: RunInjectionManager,
    run_control_manager: RunControlManager,
    role_memory_service: RoleMemoryService | None = None,
    runtime_role_resolver: RuntimeRoleResolver | None = None,
    hook_service: HookService | None = None,
    todo_service: TodoService | None = None,
    reminder_service: SystemReminderService | None = None,
) -> TaskExecutionService:
    return TaskExecutionService(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=approval_ticket_repo,
        run_runtime_repo=run_runtime_repo,
        run_event_hub=run_event_hub,
        workspace_manager=workspace_manager,
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            runtime_role_resolver=runtime_role_resolver,
            mcp_registry=mcp_registry,
            instruction_resolver=PromptInstructionResolver(
                app_config_dir=app_config_dir,
                instructions=prompt_instructions,
            ),
        ),
        provider_factory=provider_factory,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        skill_runtime_service=skill_runtime_service,
        mcp_registry=mcp_registry,
        injection_manager=injection_manager,
        run_control_manager=run_control_manager,
        role_memory_service=role_memory_service,
        runtime_role_resolver=runtime_role_resolver,
        run_intent_repo=run_intent_repo,
        media_asset_service=media_asset_service,
        hook_service=hook_service,
        todo_service=todo_service,
        reminder_service=reminder_service,
    )
