# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.providers.provider_contracts import EchoProvider, LLMProvider
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.providers.openai_compatible import OpenAICompatibleProvider
from agent_teams.providers.provider_registry import create_default_provider_registry
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.agents.execution.subagent_reflection import SubagentReflectionService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.runtime_config import RuntimeConfig
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.workspace import WorkspaceManager


def create_provider_factory(
    *,
    runtime: RuntimeConfig,
    task_repo: TaskRepository,
    shared_store: SharedStateRepository,
    event_log: EventLog,
    injection_manager: RunInjectionManager,
    run_event_hub: RunEventHub,
    agent_repo: AgentInstanceRepository,
    approval_ticket_repo: ApprovalTicketRepository,
    run_runtime_repo: RunRuntimeRepository,
    run_intent_repo: RunIntentRepository,
    workspace_manager: WorkspaceManager,
    role_memory_service: RoleMemoryService | None = None,
    subagent_reflection_service: SubagentReflectionService | None = None,
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
        config_to_use = resolve_model_profile_config(
            runtime=runtime,
            profile_name=role.model_profile,
        )
        if config_to_use is None:
            return EchoProvider()

        provider_registry = create_default_provider_registry(
            openai_compatible_builder=lambda config: OpenAICompatibleProvider(
                config,
                task_repo=task_repo,
                shared_store=shared_store,
                event_bus=event_log,
                injection_manager=injection_manager,
                run_event_hub=run_event_hub,
                agent_repo=agent_repo,
                approval_ticket_repo=approval_ticket_repo,
                run_runtime_repo=run_runtime_repo,
                run_intent_repo=run_intent_repo,
                workspace_manager=workspace_manager,
                role_memory_service=role_memory_service,
                subagent_reflection_service=subagent_reflection_service,
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
                retry_config=runtime.llm_retry,
            ),
        )
        return provider_registry.create(config_to_use)

    return provider_factory


def resolve_model_profile_config(
    *,
    runtime: RuntimeConfig,
    profile_name: str,
) -> ModelEndpointConfig | None:
    normalized_name = profile_name.strip()
    if normalized_name == "default":
        default_profile_name = runtime.default_model_profile
        if default_profile_name is None:
            return None
        return runtime.llm_profiles.get(default_profile_name)
    if normalized_name in runtime.llm_profiles:
        return runtime.llm_profiles[normalized_name]
    if runtime.default_model_profile is None:
        return None
    return runtime.llm_profiles.get(runtime.default_model_profile)
