from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.core.config import RuntimeConfig
from agent_teams.core.models import RoleDefinition
from agent_teams.mcp.registry import McpRegistry
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import (
    EchoProvider,
    LLMProvider,
    OpenAICompatibleProvider,
)
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalManager
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.tools.policy import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry


def create_provider_factory(
    *,
    runtime: RuntimeConfig,
    task_repo: TaskRepository,
    instance_pool: InstancePool,
    shared_store: SharedStore,
    event_log: EventLog,
    injection_manager: RunInjectionManager,
    run_event_hub: RunEventHub,
    agent_repo: AgentInstanceRepository,
    tool_registry: ToolRegistry,
    mcp_registry: McpRegistry,
    skill_registry: SkillRegistry,
    message_repo: MessageRepository,
    role_registry: RoleRegistry,
    run_control_manager: RunControlManager,
    tool_approval_manager: ToolApprovalManager,
    tool_approval_policy: ToolApprovalPolicy,
    get_task_execution_service: Callable[[], TaskExecutionService],
    token_usage_repo: TokenUsageRepository | None = None,
) -> Callable[[RoleDefinition], LLMProvider]:
    def provider_factory(role: RoleDefinition) -> LLMProvider:
        profile_config = runtime.llm_profiles.get(role.model_profile)
        config_to_use = profile_config or runtime.llm_profiles.get("default")
        if config_to_use is None:
            return EchoProvider()

        return OpenAICompatibleProvider(
            config_to_use,
            task_repo=task_repo,
            instance_pool=instance_pool,
            shared_store=shared_store,
            event_bus=event_log,
            injection_manager=injection_manager,
            run_event_hub=run_event_hub,
            agent_repo=agent_repo,
            workspace_root=Path.cwd(),
            tool_registry=tool_registry,
            mcp_registry=mcp_registry,
            skill_registry=skill_registry,
            allowed_tools=role.tools,
            allowed_mcp_servers=role.mcp_servers,
            allowed_skills=role.skills,
            message_repo=message_repo,
            role_registry=role_registry,
            task_execution_service=get_task_execution_service(),
            run_control_manager=run_control_manager,
            tool_approval_manager=tool_approval_manager,
            tool_approval_policy=tool_approval_policy,
            token_usage_repo=token_usage_repo,
        )

    return provider_factory


def create_task_execution_service(
    *,
    role_registry: RoleRegistry,
    instance_pool: InstancePool,
    task_repo: TaskRepository,
    shared_store: SharedStore,
    event_log: EventLog,
    agent_repo: AgentInstanceRepository,
    message_repo: MessageRepository,
    provider_factory: Callable[[RoleDefinition], LLMProvider],
    injection_manager: RunInjectionManager,
    run_control_manager: RunControlManager,
) -> TaskExecutionService:
    return TaskExecutionService(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=provider_factory,
        injection_manager=injection_manager,
        run_control_manager=run_control_manager,
    )
