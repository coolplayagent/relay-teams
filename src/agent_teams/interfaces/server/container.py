# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_teams.builtin import (
    ensure_app_config_bootstrap,
    get_builtin_roles_dir,
)
from agent_teams.agents.orchestration.meta_agent import MetaAgent
from agent_teams.agents.orchestration.coordinator import CoordinatorGraph
from agent_teams.agents.orchestration.human_gate import GateManager
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.env.environment_variable_service import EnvironmentVariableService
from agent_teams.env.proxy_config_service import ProxyConfigService
from agent_teams.env.proxy_env import ProxyEnvConfig, sync_proxy_env_to_process_env
from agent_teams.interfaces.server.config_status_service import ConfigStatusService
from agent_teams.mcp.config_manager import McpConfigManager
from agent_teams.mcp.config_reload_service import McpConfigReloadService
from agent_teams.mcp.registry import McpRegistry
from agent_teams.mcp.service import McpService
from agent_teams.notifications import NotificationConfigManager, NotificationService
from agent_teams.notifications.settings_service import NotificationSettingsService
from agent_teams.agents.execution.runtime_prompts import RuntimePromptBuilder
from agent_teams.reflection import (
    PydanticAIReflectionModelClient,
    ReflectionConfigManager,
    ReflectionJobRepository,
    ReflectionService,
)
from agent_teams.providers.contracts import LLMProvider
from agent_teams.providers.model_config_manager import ModelConfigManager
from agent_teams.providers.model_config_service import ModelConfigService
from agent_teams.providers.http_client_factory import clear_llm_http_client_cache
from agent_teams.providers.factory import create_provider_factory
from agent_teams.agents.orchestration.factory import create_task_execution_service
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles import RoleLoader, RoleRegistry
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.runs.manager import RunManager
from agent_teams.runs.runtime_config import RuntimeConfig, load_runtime_config
from agent_teams.sessions import SessionService
from agent_teams.skills.config_reload_service import SkillsConfigReloadService
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_intent_repo import RunIntentRepository
from agent_teams.state.run_runtime_repo import RunRuntimeRepository
from agent_teams.state.run_state_repo import RunStateRepository
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.tools.registry import ToolRegistry, build_default_registry
from agent_teams.tools.runtime import (
    ToolApprovalManager,
    ToolApprovalPolicy,
)
from agent_teams.triggers import TriggerRepository, TriggerService
from agent_teams.workspace import WorkspaceManager


class ServerContainer:
    def __init__(
        self,
        *,
        config_dir: Path,
        roles_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        runtime = load_runtime_config(
            config_dir=config_dir,
            roles_dir=roles_dir,
            db_path=db_path,
        )
        ensure_app_config_bootstrap(config_dir)
        self.config_dir: Path = config_dir
        self.runtime: RuntimeConfig = runtime

        self.model_config_manager: ModelConfigManager = ModelConfigManager(
            config_dir=config_dir
        )
        self.notification_config_manager: NotificationConfigManager = (
            NotificationConfigManager(config_dir=config_dir)
        )
        self.proxy_config_service: ProxyConfigService = ProxyConfigService(
            config_dir=config_dir,
            on_proxy_reloaded=self._on_proxy_reloaded,
        )
        self.environment_variable_service: EnvironmentVariableService = (
            EnvironmentVariableService()
        )
        self.mcp_config_manager: McpConfigManager = McpConfigManager(
            app_config_dir=config_dir
        )
        self.reflection_config_manager: ReflectionConfigManager = (
            ReflectionConfigManager(config_dir=config_dir)
        )
        self.role_registry: RoleRegistry = RoleLoader().load_builtin_and_app(
            builtin_roles_dir=get_builtin_roles_dir(),
            app_roles_dir=runtime.paths.roles_dir,
        )
        self.tool_registry: ToolRegistry = build_default_registry()
        self.mcp_registry: McpRegistry = self.mcp_config_manager.load_registry()
        self.mcp_service: McpService = McpService(registry=self.mcp_registry)
        self.skill_registry: SkillRegistry = SkillRegistry.from_config_dirs(
            app_config_dir=config_dir
        )

        for role in self.role_registry.list_roles():
            self.tool_registry.validate_known(role.tools)
            self.mcp_registry.validate_known(role.mcp_servers)
            self.skill_registry.validate_known(role.skills)

        self.task_repo: TaskRepository = TaskRepository(runtime.paths.db_path)
        self.shared_store: SharedStateRepository = SharedStateRepository(
            runtime.paths.db_path
        )
        self.workspace_manager: WorkspaceManager = WorkspaceManager(
            project_root=Path.cwd(),
            shared_store=self.shared_store,
        )
        self.event_log: EventLog = EventLog(runtime.paths.db_path)
        self.agent_repo: AgentInstanceRepository = AgentInstanceRepository(
            runtime.paths.db_path
        )
        self.message_repo: MessageRepository = MessageRepository(runtime.paths.db_path)
        self.approval_ticket_repo: ApprovalTicketRepository = ApprovalTicketRepository(
            runtime.paths.db_path
        )
        self.run_runtime_repo: RunRuntimeRepository = RunRuntimeRepository(
            runtime.paths.db_path
        )
        self.run_intent_repo: RunIntentRepository = RunIntentRepository(
            runtime.paths.db_path
        )
        self.run_state_repo: RunStateRepository = RunStateRepository(
            runtime.paths.db_path
        )
        self.session_repo: SessionRepository = SessionRepository(runtime.paths.db_path)
        self.token_usage_repo: TokenUsageRepository = TokenUsageRepository(
            runtime.paths.db_path
        )
        self.trigger_repo: TriggerRepository = TriggerRepository(runtime.paths.db_path)
        self.trigger_service: TriggerService = TriggerService(
            trigger_repo=self.trigger_repo
        )
        self.reflection_repo: ReflectionJobRepository = ReflectionJobRepository(
            runtime.paths.db_path
        )
        self.reflection_service: ReflectionService = ReflectionService(
            config_manager=self.reflection_config_manager,
            repository=self.reflection_repo,
            workspace_manager=self.workspace_manager,
            message_repo=self.message_repo,
            task_repo=self.task_repo,
            agent_repo=self.agent_repo,
            model_client=PydanticAIReflectionModelClient(
                llm_profiles=runtime.llm_profiles,
                get_config=self.reflection_config_manager.get_reflection_config,
            ),
        )

        self.agent_repo.mark_running_instances_failed()
        self.injection_manager: RunInjectionManager = RunInjectionManager()
        self.run_control_manager: RunControlManager = RunControlManager()
        self.run_event_hub: RunEventHub = RunEventHub(
            event_log=self.event_log,
            run_state_repo=self.run_state_repo,
        )
        self.notification_service: NotificationService = NotificationService(
            run_event_hub=self.run_event_hub,
            get_config=self.notification_config_manager.get_notification_config,
        )
        self.gate_manager: GateManager = GateManager()
        self.tool_approval_manager: ToolApprovalManager = ToolApprovalManager()
        self.tool_approval_policy: ToolApprovalPolicy = ToolApprovalPolicy()
        self.run_control_manager.bind_runtime(
            run_event_hub=self.run_event_hub,
            injection_manager=self.injection_manager,
            agent_repo=self.agent_repo,
            task_repo=self.task_repo,
            message_repo=self.message_repo,
            event_bus=self.event_log,
            run_runtime_repo=self.run_runtime_repo,
        )

        self._provider_factory: Callable[[RoleDefinition], LLMProvider]
        self.task_execution_service: TaskExecutionService
        self.task_service: TaskOrchestrationService
        self._build_runtime_services()

        coordinator = CoordinatorGraph(
            role_registry=self.role_registry,
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_bus=self.event_log,
            agent_repo=self.agent_repo,
            prompt_builder=RuntimePromptBuilder(),
            provider_factory=self._provider_factory,
            task_execution_service=self.task_execution_service,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
            gate_manager=self.gate_manager,
            run_event_hub=self.run_event_hub,
        )
        self.meta_agent: MetaAgent = MetaAgent(coordinator=coordinator)
        self.run_service: RunManager = RunManager(
            meta_agent=self.meta_agent,
            injection_manager=self.injection_manager,
            run_event_hub=self.run_event_hub,
            run_control_manager=self.run_control_manager,
            tool_approval_manager=self.tool_approval_manager,
            session_repo=self.session_repo,
            event_log=self.event_log,
            task_repo=self.task_repo,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_intent_repo=self.run_intent_repo,
            run_state_repo=self.run_state_repo,
            notification_service=self.notification_service,
        )
        self.session_service: SessionService = SessionService(
            session_repo=self.session_repo,
            task_repo=self.task_repo,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            token_usage_repo=self.token_usage_repo,
            run_event_hub=self.run_event_hub,
            resolve_active_run_id=lambda session_id: (
                self.run_service._active_run_by_session.get(session_id)
            ),
            event_log=self.event_log,
            shared_store=self.shared_store,
            workspace_manager=self.workspace_manager,
            reflection_service=self.reflection_service,
        )
        self.config_status_service: ConfigStatusService = ConfigStatusService(
            get_runtime=lambda: self.runtime,
            get_mcp_registry=lambda: self.mcp_registry,
            get_skill_registry=lambda: self.skill_registry,
            get_proxy_status=self.proxy_config_service.get_proxy_status,
        )
        self.model_config_service: ModelConfigService = ModelConfigService(
            config_dir=config_dir,
            roles_dir=self.runtime.paths.roles_dir,
            db_path=self.runtime.paths.db_path,
            model_config_manager=self.model_config_manager,
            get_runtime=lambda: self.runtime,
            on_runtime_reloaded=self._on_runtime_reloaded,
        )
        self.notification_settings_service: NotificationSettingsService = (
            NotificationSettingsService(
                notification_config_manager=self.notification_config_manager
            )
        )
        self.role_settings_service: RoleSettingsService = RoleSettingsService(
            roles_dir=self.runtime.paths.roles_dir,
            builtin_roles_dir=get_builtin_roles_dir(),
            get_tool_registry=lambda: self.tool_registry,
            get_mcp_registry=lambda: self.mcp_registry,
            get_skill_registry=lambda: self.skill_registry,
            on_roles_reloaded=self._on_roles_reloaded,
        )
        self.mcp_config_reload_service: McpConfigReloadService = McpConfigReloadService(
            mcp_config_manager=self.mcp_config_manager,
            role_registry=self.role_registry,
            on_mcp_reloaded=self._on_mcp_reloaded,
        )
        self.skills_config_reload_service: SkillsConfigReloadService = (
            SkillsConfigReloadService(
                config_dir=config_dir,
                role_registry=self.role_registry,
                on_skill_reloaded=self._on_skill_reloaded,
            )
        )

    def _build_runtime_services(self) -> None:
        def get_task_execution_service() -> TaskExecutionService:
            return self.task_execution_service

        def get_task_service() -> TaskOrchestrationService:
            return self.task_service

        self._provider_factory = create_provider_factory(
            runtime=self.runtime,
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_log=self.event_log,
            injection_manager=self.injection_manager,
            run_event_hub=self.run_event_hub,
            agent_repo=self.agent_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            workspace_manager=self.workspace_manager,
            tool_registry=self.tool_registry,
            mcp_registry=self.mcp_registry,
            skill_registry=self.skill_registry,
            message_repo=self.message_repo,
            role_registry=self.role_registry,
            get_task_service=get_task_service,
            run_control_manager=self.run_control_manager,
            tool_approval_manager=self.tool_approval_manager,
            tool_approval_policy=self.tool_approval_policy,
            notification_service=self.notification_service,
            get_task_execution_service=get_task_execution_service,
            token_usage_repo=self.token_usage_repo,
        )
        self.task_execution_service = create_task_execution_service(
            role_registry=self.role_registry,
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_log=self.event_log,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            workspace_manager=self.workspace_manager,
            provider_factory=self._provider_factory,
            injection_manager=self.injection_manager,
            run_control_manager=self.run_control_manager,
            reflection_service=self.reflection_service,
        )
        self.task_service = TaskOrchestrationService(
            task_repo=self.task_repo,
            role_registry=self.role_registry,
            agent_repo=self.agent_repo,
            task_execution_service=self.task_execution_service,
            message_repo=self.message_repo,
        )

    async def start(self) -> None:
        await self.reflection_service.start()

    async def stop(self) -> None:
        await self.reflection_service.stop()

    def _refresh_coordinator_runtime(self) -> None:
        self._build_runtime_services()
        self.meta_agent.coordinator.role_registry = self.role_registry
        self.meta_agent.coordinator.provider_factory = self._provider_factory
        self.meta_agent.coordinator.task_execution_service = self.task_execution_service

    def _on_runtime_reloaded(self, runtime: RuntimeConfig) -> None:
        self.runtime = runtime
        self.reflection_service.replace_llm_profiles(runtime.llm_profiles)
        self._refresh_coordinator_runtime()

    def _on_roles_reloaded(self, role_registry: RoleRegistry) -> None:
        self.role_registry = role_registry
        self.mcp_config_reload_service = McpConfigReloadService(
            mcp_config_manager=self.mcp_config_manager,
            role_registry=self.role_registry,
            on_mcp_reloaded=self._on_mcp_reloaded,
        )
        self.skills_config_reload_service = SkillsConfigReloadService(
            config_dir=self.config_dir,
            role_registry=self.role_registry,
            on_skill_reloaded=self._on_skill_reloaded,
        )
        self._refresh_coordinator_runtime()

    def _on_mcp_reloaded(self, mcp_registry: McpRegistry) -> None:
        self.mcp_registry = mcp_registry
        self.mcp_service.replace_registry(mcp_registry)
        self._refresh_coordinator_runtime()

    def _on_skill_reloaded(self, skill_registry: SkillRegistry) -> None:
        self.skill_registry = skill_registry
        self._refresh_coordinator_runtime()

    def _on_proxy_reloaded(self, proxy_config: ProxyEnvConfig) -> None:
        sync_proxy_env_to_process_env(proxy_config)
        clear_llm_http_client_cache()
        self._on_mcp_reloaded(self.mcp_config_manager.load_registry())
