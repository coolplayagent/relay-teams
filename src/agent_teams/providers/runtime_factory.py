# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.mcp.registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import (
    EchoProvider,
    LLMProvider,
    OpenAICompatibleProvider,
)
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.providers.registry import create_default_provider_registry
from agent_teams.reflection.service import ReflectionService
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.runs.runtime_config import RuntimeConfig
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import RunRuntimeRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.workspace import WorkspaceManager


def create_provider_factory(
    *,
    runtime: RuntimeConfig,
    task_repo: TaskRepository,
    instance_pool: InstancePool,
    shared_store: SharedStateRepository,
    event_log: EventLog,
    injection_manager: RunInjectionManager,
    run_event_hub: RunEventHub,
    agent_repo: AgentInstanceRepository,
    approval_ticket_repo: ApprovalTicketRepository,
    run_runtime_repo: RunRuntimeRepository,
    workspace_manager: WorkspaceManager,
    tool_registry: ToolRegistry,
    mcp_registry: McpRegistry,
    skill_registry: SkillRegistry,
    message_repo: MessageRepository,
    role_registry: RoleRegistry,
    get_task_service: Callable[[], TaskOrchestrationService],
    run_control_manager: RunControlManager,
    tool_approval_manager: ToolApprovalManager,
    tool_approval_policy: ToolApprovalPolicy,
    notification_service: NotificationService | None,
    get_task_execution_service: Callable[[], TaskExecutionService],
    token_usage_repo: TokenUsageRepository | None = None,
) -> Callable[[RoleDefinition], LLMProvider]:
    def provider_factory(role: RoleDefinition) -> LLMProvider:
        config_to_use = _resolve_model_profile_config(
            llm_profiles=runtime.llm_profiles,
            profile_name=role.model_profile,
        )
        if config_to_use is None:
            return EchoProvider()

        provider_registry = create_default_provider_registry(
            openai_compatible_builder=lambda config: OpenAICompatibleProvider(
                config,
                task_repo=task_repo,
                instance_pool=instance_pool,
                shared_store=shared_store,
                event_bus=event_log,
                injection_manager=injection_manager,
                run_event_hub=run_event_hub,
                agent_repo=agent_repo,
                approval_ticket_repo=approval_ticket_repo,
                run_runtime_repo=run_runtime_repo,
                workspace_manager=workspace_manager,
                tool_registry=tool_registry,
                mcp_registry=mcp_registry,
                skill_registry=skill_registry,
                allowed_tools=role.tools,
                allowed_mcp_servers=role.mcp_servers,
                allowed_skills=role.skills,
                message_repo=message_repo,
                role_registry=role_registry,
                task_execution_service=get_task_execution_service(),
                task_service=get_task_service(),
                run_control_manager=run_control_manager,
                tool_approval_manager=tool_approval_manager,
                tool_approval_policy=tool_approval_policy,
                notification_service=notification_service,
                token_usage_repo=token_usage_repo,
            ),
        )
        return provider_registry.create(config_to_use)

    return provider_factory


def _resolve_model_profile_config(
    *,
    llm_profiles: dict[str, ModelEndpointConfig],
    profile_name: str,
) -> ModelEndpointConfig | None:
    if profile_name in llm_profiles:
        return llm_profiles[profile_name]
    return llm_profiles.get("default")


def create_task_execution_service(
    *,
    role_registry: RoleRegistry,
    instance_pool: InstancePool,
    task_repo: TaskRepository,
    shared_store: SharedStateRepository,
    event_log: EventLog,
    agent_repo: AgentInstanceRepository,
    message_repo: MessageRepository,
    approval_ticket_repo: ApprovalTicketRepository,
    run_runtime_repo: RunRuntimeRepository,
    workspace_manager: WorkspaceManager,
    provider_factory: Callable[[RoleDefinition], LLMProvider],
    injection_manager: RunInjectionManager,
    run_control_manager: RunControlManager,
    reflection_service: ReflectionService | None = None,
) -> TaskExecutionService:
    return TaskExecutionService(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=approval_ticket_repo,
        run_runtime_repo=run_runtime_repo,
        workspace_manager=workspace_manager,
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=provider_factory,
        injection_manager=injection_manager,
        run_control_manager=run_control_manager,
        reflection_service=reflection_service,
    )
