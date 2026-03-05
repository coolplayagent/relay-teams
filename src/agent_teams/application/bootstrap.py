from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from agent_teams.agents.core.meta_agent import MetaAgent
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.application.config_manager import ConfigManager
from agent_teams.application.provider_runtime import (
    create_provider_factory,
    create_task_execution_service,
)
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.coordination.coordinator import CoordinatorGraph
from agent_teams.core.config import RuntimeConfig, load_runtime_config
from agent_teams.core.models import RoleDefinition
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.roles.registry import RoleLoader
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.gate_manager import GateManager
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.tools.defaults import build_default_registry
from agent_teams.tools.policy import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry
from agent_teams.mcp.registry import McpRegistry
from agent_teams.skills.registry import SkillRegistry
from agent_teams.providers.llm import LLMProvider


class ServiceComponents(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    runtime: RuntimeConfig
    config_manager: ConfigManager
    role_registry: RoleRegistry
    tool_registry: ToolRegistry
    mcp_registry: McpRegistry
    skill_registry: SkillRegistry
    task_repo: TaskRepository
    shared_store: SharedStore
    event_log: EventLog
    agent_repo: AgentInstanceRepository
    message_repo: MessageRepository
    session_repo: SessionRepository
    instance_pool: InstancePool
    injection_manager: RunInjectionManager
    run_control_manager: RunControlManager
    run_event_hub: RunEventHub
    gate_manager: GateManager
    tool_approval_manager: ToolApprovalManager
    tool_approval_policy: ToolApprovalPolicy
    provider_factory: Callable[[RoleDefinition], LLMProvider]
    task_execution_service: TaskExecutionService
    meta_agent: MetaAgent
    token_usage_repo: TokenUsageRepository


def build_service_components(
    *,
    config_dir: Path,
    roles_dir: Path | None,
    db_path: Path | None,
) -> ServiceComponents:
    runtime = load_runtime_config(
        config_dir=config_dir, roles_dir=roles_dir, db_path=db_path
    )
    config_manager = ConfigManager(config_dir=config_dir)

    role_registry = RoleLoader().load_all(runtime.paths.roles_dir)
    tool_registry = build_default_registry()
    mcp_registry = config_manager.load_mcp_registry()
    skill_registry = config_manager.load_skill_registry()

    for role in role_registry.list_roles():
        tool_registry.validate_known(role.tools)
        mcp_registry.validate_known(role.mcp_servers)
        skill_registry.validate_known(role.skills)

    task_repo = TaskRepository(runtime.paths.db_path)
    shared_store = SharedStore(runtime.paths.db_path)
    event_log = EventLog(runtime.paths.db_path)
    agent_repo = AgentInstanceRepository(runtime.paths.db_path)
    message_repo = MessageRepository(runtime.paths.db_path)
    session_repo = SessionRepository(runtime.paths.db_path)
    token_usage_repo = TokenUsageRepository(runtime.paths.db_path)
    instance_pool = InstancePool.from_repo(agent_repo)
    injection_manager = RunInjectionManager()
    run_control_manager = RunControlManager()
    run_event_hub = RunEventHub(event_log=event_log)
    gate_manager = GateManager()
    tool_approval_manager = ToolApprovalManager()
    tool_approval_policy = ToolApprovalPolicy()
    run_control_manager.bind_runtime(
        run_event_hub=run_event_hub,
        injection_manager=injection_manager,
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=message_repo,
        instance_pool=instance_pool,
        event_bus=event_log,
    )

    prompt_builder = RuntimePromptBuilder()
    task_execution_service = None

    def get_task_execution_service():
        if task_execution_service is None:
            raise RuntimeError("TaskExecutionService not initialized")
        return task_execution_service

    provider_factory = create_provider_factory(
        runtime=runtime,
        task_repo=task_repo,
        instance_pool=instance_pool,
        shared_store=shared_store,
        event_log=event_log,
        injection_manager=injection_manager,
        run_event_hub=run_event_hub,
        agent_repo=agent_repo,
        tool_registry=tool_registry,
        mcp_registry=mcp_registry,
        skill_registry=skill_registry,
        message_repo=message_repo,
        role_registry=role_registry,
        run_control_manager=run_control_manager,
        tool_approval_manager=tool_approval_manager,
        tool_approval_policy=tool_approval_policy,
        get_task_execution_service=get_task_execution_service,
        token_usage_repo=token_usage_repo,
    )
    task_execution_service = create_task_execution_service(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_log=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        provider_factory=provider_factory,
        injection_manager=injection_manager,
        run_control_manager=run_control_manager,
    )

    coordinator = CoordinatorGraph(
        role_registry=role_registry,
        instance_pool=instance_pool,
        task_repo=task_repo,
        shared_store=shared_store,
        event_bus=event_log,
        agent_repo=agent_repo,
        prompt_builder=prompt_builder,
        provider_factory=provider_factory,
        task_execution_service=task_execution_service,
        run_control_manager=run_control_manager,
        gate_manager=gate_manager,
        run_event_hub=run_event_hub,
    )
    meta_agent = MetaAgent(coordinator=coordinator)

    return ServiceComponents(
        runtime=runtime,
        config_manager=config_manager,
        role_registry=role_registry,
        tool_registry=tool_registry,
        mcp_registry=mcp_registry,
        skill_registry=skill_registry,
        task_repo=task_repo,
        shared_store=shared_store,
        event_log=event_log,
        agent_repo=agent_repo,
        message_repo=message_repo,
        session_repo=session_repo,
        instance_pool=instance_pool,
        injection_manager=injection_manager,
        run_control_manager=run_control_manager,
        run_event_hub=run_event_hub,
        gate_manager=gate_manager,
        tool_approval_manager=tool_approval_manager,
        tool_approval_policy=tool_approval_policy,
        provider_factory=provider_factory,
        task_execution_service=task_execution_service,
        meta_agent=meta_agent,
        token_usage_repo=token_usage_repo,
    )
