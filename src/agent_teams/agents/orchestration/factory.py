# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from agent_teams.agents.execution.system_prompts import RuntimePromptBuilder
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.mcp.registry import McpRegistry
from agent_teams.providers.contracts import LLMProvider
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.sessions.runs.control import RunControlManager
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.agents.agent_repo import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repo import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repo import TaskRepository
from agent_teams.workspace import WorkspaceManager


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
    run_intent_repo: RunIntentRepository,
    workspace_manager: WorkspaceManager,
    provider_factory: Callable[[RoleDefinition], LLMProvider],
    mcp_registry: McpRegistry,
    injection_manager: RunInjectionManager,
    run_control_manager: RunControlManager,
    role_memory_service: RoleMemoryService | None = None,
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
        workspace_manager=workspace_manager,
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=mcp_registry,
        ),
        provider_factory=provider_factory,
        injection_manager=injection_manager,
        run_control_manager=run_control_manager,
        role_memory_service=role_memory_service,
        run_intent_repo=run_intent_repo,
    )
