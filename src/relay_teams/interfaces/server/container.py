# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from relay_teams.agents.execution.prompt_instructions import PromptInstructionResolver
from relay_teams.automation import (
    AutomationBoundSessionQueueRepository,
    AutomationBoundSessionQueueService,
    AutomationBoundSessionQueueWorker,
    AutomationDeliveryRepository,
    AutomationEventRepository,
    AutomationDeliveryService,
    AutomationDeliveryWorker,
    AutomationFeishuBindingService,
    AutomationProjectRepository,
    AutomationSchedulerService,
    AutomationService,
)
from relay_teams.builtin import (
    ensure_app_config_bootstrap,
    get_builtin_roles_dir,
    get_builtin_skills_dir,
)
from relay_teams.computer import build_default_computer_runtime
from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.agents.orchestration import (
    OrchestrationSettingsConfigManager,
    OrchestrationSettingsService,
)
from relay_teams.agents.orchestration.coordinator import CoordinatorGraph
from relay_teams.agents.orchestration.human_gate import GateManager
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.env.clawhub_config_service import ClawHubConfigService
from relay_teams.env.environment_variable_service import EnvironmentVariableService
from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.localhost_run_tunnel_service import LocalhostRunTunnelService
from relay_teams.env.proxy_config_service import ProxyConfigService
from relay_teams.env.proxy_env import ProxyEnvConfig, sync_proxy_env_to_process_env
from relay_teams.env.web_config_service import WebConfigService
from relay_teams.external_agents import (
    ExternalAgentConfigService,
    ExternalAgentSessionRepository,
)
from relay_teams.external_agents.provider import ExternalAcpSessionManager
from relay_teams.gateway.feishu import (
    FeishuAccountRepository,
    FeishuClient,
    FeishuGatewayService,
    FeishuInboundRuntime,
    FeishuMessagePoolRepository,
    FeishuMessagePoolService,
    FeishuNotificationDispatcher,
    FeishuSubscriptionService,
    FeishuTriggerHandler,
)
from relay_teams.gateway.feishu.notification_delivery import (
    CompositeTerminalNotificationSuppressor,
)
from relay_teams.gateway.im import (
    ImSessionCommandService,
    ImToolContextResolver,
    ImToolService,
)
from relay_teams.gateway.gateway_session_repository import GatewaySessionRepository
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.session_ingress_service import GatewaySessionIngressService
from relay_teams.logger import get_logger
from relay_teams.interfaces.server.config_status_service import ConfigStatusService
from relay_teams.interfaces.server.ui_language_service import UiLanguageSettingsService
from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.config_reload_service import McpConfigReloadService
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.mcp.mcp_service import McpService
from relay_teams.metrics import (
    AggregateStoreSink,
    DEFAULT_DEFINITIONS,
    GrafanaExporterSink,
    MetricRecorder,
    MetricRegistry,
    MetricsQueryService,
    MetricsService,
    PrettyLogSink,
    SqliteMetricAggregateStore,
)
from relay_teams.media import MediaAssetRepository, MediaAssetService
from relay_teams.monitors import MonitorRepository, MonitorService
from relay_teams.notifications import NotificationConfigManager, NotificationService
from relay_teams.notifications.notification_settings_service import (
    NotificationSettingsService,
)
from relay_teams.agents.execution.system_prompts import RuntimePromptBuilder
from relay_teams.retrieval import RetrievalService, SqliteFts5RetrievalStore
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.providers.model_config_manager import ModelConfigManager
from relay_teams.providers.model_config_service import ModelConfigService
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.model_fallback_config_manager import (
    ModelFallbackConfigManager,
)
from relay_teams.providers.model_fallback import LlmFallbackMiddleware
from relay_teams.net.llm_client import clear_llm_http_client_cache
from relay_teams.providers.provider_factory import (
    apply_default_model_profile_override,
    create_provider_factory,
    resolve_model_profile_config,
)
from relay_teams.agents.orchestration.task_execution_service_factory import (
    create_task_execution_service,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles import (
    RoleLoader,
    RoleMemoryRepository,
    RoleMemoryService,
    RoleRegistry,
    RuntimeRoleResolver,
    TemporaryRoleRepository,
)
from relay_teams.roles.settings_service import RoleSettingsService
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.runtime_config import RuntimeConfig, load_runtime_config
from relay_teams.sessions import (
    ExternalSessionBindingRepository,
    SessionHistoryMarkerRepository,
    SessionService,
)
from relay_teams.skills.config_reload_service import SkillsConfigReloadService
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.skills.skill_routing_service import SkillRuntimeService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.subagent_reflection import SubagentReflectionService
from relay_teams.sessions.runs.background_tasks.manager import (
    BackgroundTaskManager,
)
from relay_teams.sessions.runs.background_tasks.command_runtime import (
    kill_process_tree_by_pid,
)
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.tools.registry import (
    ToolRegistry,
    ToolResolutionContext,
    build_default_registry,
)
from relay_teams.tools.runtime import (
    ToolApprovalManager,
    ToolApprovalPolicy,
)
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.triggers import (
    GitHubApiClient,
    GitHubTriggerActionWorker,
    GitHubTriggerService,
    TriggerRepository,
    get_github_trigger_secret_store,
)
from relay_teams.gateway.wechat import (
    WeChatAccountRepository,
    WeChatClient,
    WeChatGatewayService,
    WeChatInboundQueueRepository,
    get_wechat_secret_store,
)
from relay_teams.hooks import HookLoader, HookRuntimeState, HookService
from relay_teams.hooks.executors.command_executor import CommandHookExecutor
from relay_teams.hooks.executors.http_executor import HttpHookExecutor
from relay_teams.workspace import (
    SshProfileRepository,
    SshProfileService,
    WorkspaceManager,
    WorkspaceRepository,
    WorkspaceService,
)


LOGGER = get_logger(__name__)


class ServerContainer:
    def __init__(
        self,
        *,
        config_dir: Path,
        roles_dir: Path | None = None,
        db_path: Path | None = None,
        manage_runtime_state: bool = True,
        session_model_profile_lookup: Callable[[str], ModelEndpointConfig | None]
        | None = None,
    ) -> None:
        runtime = load_runtime_config(
            config_dir=config_dir,
            roles_dir=roles_dir,
            db_path=db_path,
        )
        app_config_dir = runtime.paths.config_dir
        ensure_app_config_bootstrap(app_config_dir)
        self.config_dir: Path = app_config_dir
        self.runtime: RuntimeConfig = runtime
        self._session_model_profile_lookup = session_model_profile_lookup

        self.model_config_manager: ModelConfigManager = ModelConfigManager(
            config_dir=app_config_dir
        )
        self.model_fallback_config_manager = ModelFallbackConfigManager(
            config_dir=app_config_dir
        )
        self.notification_config_manager: NotificationConfigManager = (
            NotificationConfigManager(config_dir=app_config_dir)
        )
        self.orchestration_settings_config_manager = OrchestrationSettingsConfigManager(
            config_dir=app_config_dir
        )
        self.proxy_config_service: ProxyConfigService = ProxyConfigService(
            config_dir=app_config_dir,
            on_proxy_reloaded=self._on_proxy_reloaded,
        )
        self.hook_service = HookService(
            loader=HookLoader(app_config_dir=app_config_dir, project_root=Path.cwd()),
            runtime_state=HookRuntimeState(),
            command_executor=CommandHookExecutor(),
            http_executor=HttpHookExecutor(
                get_proxy_config=self.proxy_config_service.get_proxy_config
            ),
        )
        self.web_config_service: WebConfigService = WebConfigService(
            config_dir=app_config_dir
        )
        self.github_config_service: GitHubConfigService = GitHubConfigService(
            config_dir=app_config_dir,
            get_proxy_config=self.proxy_config_service.get_proxy_config,
        )
        self.localhost_run_tunnel_service = LocalhostRunTunnelService()
        self.clawhub_config_service: ClawHubConfigService = ClawHubConfigService(
            config_dir=app_config_dir
        )
        self.ui_language_settings_service = UiLanguageSettingsService(
            config_dir=app_config_dir
        )
        self.external_agent_config_service = ExternalAgentConfigService(
            config_dir=app_config_dir
        )
        self.environment_variable_service: EnvironmentVariableService = (
            EnvironmentVariableService(
                app_env_file_path=runtime.paths.env_file,
                on_app_env_changed=self._on_app_environment_changed,
            )
        )
        self.mcp_config_manager: McpConfigManager = McpConfigManager(
            app_config_dir=app_config_dir
        )
        self.tool_registry: ToolRegistry = build_default_registry()
        self.mcp_registry: McpRegistry = self.mcp_config_manager.load_registry()
        self.mcp_service: McpService = McpService(registry=self.mcp_registry)
        self.skill_registry: SkillRegistry = SkillRegistry.from_config_dirs(
            app_config_dir=app_config_dir
        )
        self.role_registry = self._sanitize_role_registry(
            RoleLoader().load_builtin_and_app(
                builtin_roles_dir=get_builtin_roles_dir(),
                app_roles_dir=runtime.paths.roles_dir,
                allow_empty=True,
            )
        )

        self.task_repo: TaskRepository = TaskRepository(runtime.paths.db_path)
        self.shared_store: SharedStateRepository = SharedStateRepository(
            runtime.paths.db_path
        )
        self.workspace_repo: WorkspaceRepository = WorkspaceRepository(
            runtime.paths.db_path
        )
        self.ssh_profile_repo: SshProfileRepository = SshProfileRepository(
            runtime.paths.db_path
        )
        self.ssh_profile_service: SshProfileService = SshProfileService(
            repository=self.ssh_profile_repo
        )
        self.workspace_service: WorkspaceService = WorkspaceService(
            repository=self.workspace_repo,
            ssh_profile_service=self.ssh_profile_service,
        )
        self.workspace_manager: WorkspaceManager = WorkspaceManager(
            project_root=Path.cwd(),
            app_config_dir=app_config_dir,
            workspace_repo=self.workspace_repo,
            builtin_skills_dir=get_builtin_skills_dir(),
            app_skills_dir=app_config_dir / "skills",
        )
        self.media_asset_repo: MediaAssetRepository = MediaAssetRepository(
            runtime.paths.db_path
        )
        self.media_asset_service: MediaAssetService = MediaAssetService(
            repository=self.media_asset_repo,
            workspace_manager=self.workspace_manager,
        )
        self.computer_runtime = build_default_computer_runtime(project_root=Path.cwd())
        self.event_log: EventLog = EventLog(runtime.paths.db_path)
        self.agent_repo: AgentInstanceRepository = AgentInstanceRepository(
            runtime.paths.db_path
        )
        self.session_history_marker_repo: SessionHistoryMarkerRepository = (
            SessionHistoryMarkerRepository(runtime.paths.db_path)
        )
        self.message_repo: MessageRepository = MessageRepository(
            runtime.paths.db_path,
            session_history_marker_repo=self.session_history_marker_repo,
        )
        self.approval_ticket_repo: ApprovalTicketRepository = ApprovalTicketRepository(
            runtime.paths.db_path
        )
        self.shell_approval_repo: ShellApprovalRepository = ShellApprovalRepository(
            runtime.paths.db_path
        )
        self.run_runtime_repo: RunRuntimeRepository = RunRuntimeRepository(
            runtime.paths.db_path
        )
        self.run_intent_repo: RunIntentRepository = RunIntentRepository(
            runtime.paths.db_path
        )
        self.background_task_repository: BackgroundTaskRepository = (
            BackgroundTaskRepository(runtime.paths.db_path)
        )
        self.run_state_repo: RunStateRepository = RunStateRepository(
            runtime.paths.db_path
        )
        self.trigger_repository = TriggerRepository(runtime.paths.db_path)
        self.session_repo: SessionRepository = SessionRepository(runtime.paths.db_path)
        self.external_session_binding_repo: ExternalSessionBindingRepository = (
            ExternalSessionBindingRepository(runtime.paths.db_path)
        )
        self.external_agent_session_repo = ExternalAgentSessionRepository(
            runtime.paths.db_path
        )
        self.gateway_session_repository = GatewaySessionRepository(
            runtime.paths.db_path
        )
        self.orchestration_settings_service: OrchestrationSettingsService = (
            OrchestrationSettingsService(
                config_manager=self.orchestration_settings_config_manager,
                session_repo=self.session_repo,
                get_role_registry=lambda: self.role_registry,
            )
        )
        self.token_usage_repo: TokenUsageRepository = TokenUsageRepository(
            runtime.paths.db_path,
            session_history_marker_repo=self.session_history_marker_repo,
        )
        self.metric_registry: MetricRegistry = MetricRegistry(DEFAULT_DEFINITIONS)
        self.metrics_store: SqliteMetricAggregateStore = SqliteMetricAggregateStore(
            runtime.paths.db_path
        )
        self.metric_recorder: MetricRecorder = MetricRecorder(
            registry=self.metric_registry,
            sinks=(
                AggregateStoreSink(self.metrics_store),
                PrettyLogSink(),
                GrafanaExporterSink(),
            ),
        )
        self.retrieval_store = SqliteFts5RetrievalStore(runtime.paths.db_path)
        self.retrieval_service = RetrievalService(
            store=self.retrieval_store,
            metric_recorder=self.metric_recorder,
        )
        self.skill_runtime_service = self._build_skill_runtime_service(
            skill_registry=self.skill_registry
        )
        self.metrics_query_service: MetricsQueryService = MetricsQueryService(
            store=self.metrics_store
        )
        self.metrics_service: MetricsService = MetricsService(
            query_service=self.metrics_query_service
        )
        self.feishu_message_pool_repo: FeishuMessagePoolRepository = (
            FeishuMessagePoolRepository(runtime.paths.db_path)
        )
        self.feishu_account_repository = FeishuAccountRepository(runtime.paths.db_path)
        self.automation_repo: AutomationProjectRepository = AutomationProjectRepository(
            runtime.paths.db_path
        )
        self.automation_event_repo = AutomationEventRepository(runtime.paths.db_path)
        self.automation_delivery_repo: AutomationDeliveryRepository = (
            AutomationDeliveryRepository(runtime.paths.db_path)
        )
        self.automation_bound_session_queue_repo: AutomationBoundSessionQueueRepository = AutomationBoundSessionQueueRepository(
            runtime.paths.db_path
        )
        self.role_memory_repo: RoleMemoryRepository = RoleMemoryRepository(
            runtime.paths.db_path
        )
        self.role_memory_service: RoleMemoryService = RoleMemoryService(
            repository=self.role_memory_repo
        )
        self.temporary_role_repo: TemporaryRoleRepository = TemporaryRoleRepository(
            runtime.paths.db_path
        )
        self.runtime_role_resolver: RuntimeRoleResolver = RuntimeRoleResolver(
            role_registry=self.role_registry,
            temporary_role_repository=self.temporary_role_repo,
        )
        self.subagent_reflection_service = self._build_subagent_reflection_service()
        self._ensure_default_workspace()

        if manage_runtime_state:
            self.agent_repo.mark_running_instances_failed()
            _ = self.run_runtime_repo.mark_transient_runs_interrupted()
            self._interrupt_transient_background_tasks()
        self.injection_manager: RunInjectionManager = RunInjectionManager()
        self.run_control_manager: RunControlManager = RunControlManager()
        self.active_run_registry: ActiveSessionRunRegistry = ActiveSessionRunRegistry(
            run_runtime_repo=self.run_runtime_repo
        )
        self.run_event_hub: RunEventHub = RunEventHub(
            event_log=self.event_log,
            run_state_repo=self.run_state_repo,
        )
        self.monitor_repository = MonitorRepository(runtime.paths.db_path)
        self.monitor_service = MonitorService(
            repository=self.monitor_repository,
            run_event_hub=self.run_event_hub,
        )
        self.background_task_manager = BackgroundTaskManager(
            repository=self.background_task_repository,
            run_event_hub=self.run_event_hub,
            monitor_service=self.monitor_service,
        )
        self.background_task_service = BackgroundTaskService(
            background_task_manager=self.background_task_manager,
            repository=self.background_task_repository,
            run_event_hub=self.run_event_hub,
        )
        self.feishu_client = FeishuClient()
        self.wechat_account_repository = WeChatAccountRepository(runtime.paths.db_path)
        self.wechat_inbound_queue_repo = WeChatInboundQueueRepository(
            runtime.paths.db_path
        )
        self.wechat_client = WeChatClient()
        self.github_trigger_secret_store = get_github_trigger_secret_store()
        self.github_api_client = GitHubApiClient(
            get_proxy_config=self.proxy_config_service.get_proxy_config
        )
        self.feishu_gateway_service = FeishuGatewayService(
            config_dir=app_config_dir,
            repository=self.feishu_account_repository,
            secret_store=None,
            role_registry=self.role_registry,
            orchestration_settings_service=self.orchestration_settings_service,
            workspace_service=self.workspace_service,
            external_session_binding_repo=self.external_session_binding_repo,
        )
        self.im_tool_service: ImToolService = ImToolService(
            config_dir=app_config_dir,
            session_repo=self.session_repo,
            runtime_config_lookup=self.feishu_gateway_service,
            automation_project_repo=self.automation_repo,
            automation_delivery_lookup=self.automation_delivery_repo,
            gateway_session_lookup=self.gateway_session_repository,
            feishu_client=self.feishu_client,
            wechat_account_repo=self.wechat_account_repository,
            wechat_secret_store=get_wechat_secret_store(),
            wechat_client=self.wechat_client,
        )
        self.tool_registry.register_implicit_resolver(
            ImToolContextResolver(
                session_repo=self.session_repo,
                runtime_config_lookup=self.feishu_gateway_service,
                automation_project_repo=self.automation_repo,
                gateway_session_lookup=self.gateway_session_repository,
            )
        )
        self.notification_service: NotificationService = NotificationService(
            run_event_hub=self.run_event_hub,
            get_config=self.notification_config_manager.get_notification_config,
            dispatchers=(
                FeishuNotificationDispatcher(
                    session_repo=self.session_repo,
                    runtime_config_lookup=self.feishu_gateway_service,
                    feishu_client=self.feishu_client,
                    terminal_notification_suppressor=None,
                ),
            ),
        )
        self.gate_manager: GateManager = GateManager()
        self.tool_approval_manager: ToolApprovalManager = ToolApprovalManager()
        self.tool_approval_policy: ToolApprovalPolicy = ToolApprovalPolicy()
        self.external_acp_session_manager = ExternalAcpSessionManager(
            config_dir=self.config_dir,
            config_service=self.external_agent_config_service,
            session_repo=self.external_agent_session_repo,
            message_repo=self.message_repo,
            run_event_hub=self.run_event_hub,
            workspace_manager=self.workspace_manager,
            media_asset_service=self.media_asset_service,
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_bus=self.event_log,
            injection_manager=self.injection_manager,
            agent_repo=self.agent_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_intent_repo=self.run_intent_repo,
            background_task_service=self.background_task_service,
            monitor_service=self.monitor_service,
            role_memory_service=self.role_memory_service,
            tool_registry=self.tool_registry,
            get_mcp_registry=lambda: self.mcp_registry,
            get_skill_registry=lambda: self.skill_registry,
            get_role_registry=lambda: self.role_registry,
            get_task_execution_service=lambda: self.task_execution_service,
            get_task_service=lambda: self.task_service,
            run_control_manager=self.run_control_manager,
            tool_approval_manager=self.tool_approval_manager,
            tool_approval_policy=self.tool_approval_policy,
            shell_approval_repo=self.shell_approval_repo,
            get_notification_service=lambda: self.notification_service,
            resolve_model_config=self._resolve_external_agent_model_config,
            metric_recorder=self.metric_recorder,
            im_tool_service=self.im_tool_service,
            computer_runtime=self.computer_runtime,
        )
        self.run_control_manager.bind_runtime(
            run_event_hub=self.run_event_hub,
            injection_manager=self.injection_manager,
            agent_repo=self.agent_repo,
            task_repo=self.task_repo,
            message_repo=self.message_repo,
            event_bus=self.event_log,
            run_runtime_repo=self.run_runtime_repo,
        )

        self._provider_factory: Callable[[RoleDefinition, str | None], LLMProvider]
        self.task_execution_service: TaskExecutionService
        self.task_service: TaskOrchestrationService
        self._build_runtime_services()
        self.background_task_service.replace_subagent_runtime_dependencies(
            task_execution_service=self.task_execution_service,
            agent_repo=self.agent_repo,
            task_repo=self.task_repo,
            run_intent_repo=self.run_intent_repo,
            run_control_manager=self.run_control_manager,
            run_runtime_repo=self.run_runtime_repo,
        )

        coordinator = CoordinatorGraph(
            role_registry=self.role_registry,
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_bus=self.event_log,
            agent_repo=self.agent_repo,
            prompt_builder=RuntimePromptBuilder(
                role_registry=self.role_registry,
                mcp_registry=self.mcp_registry,
                instruction_resolver=PromptInstructionResolver(
                    app_config_dir=runtime.paths.config_dir,
                    instructions=runtime.prompt_instructions.instructions,
                ),
            ),
            provider_factory=self._provider_factory,
            task_execution_service=self.task_execution_service,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
            session_repo=self.session_repo,
            gate_manager=self.gate_manager,
            run_event_hub=self.run_event_hub,
        )
        self.meta_agent: MetaAgent = MetaAgent(coordinator=coordinator)
        self.run_service: RunManager = RunManager(
            meta_agent=self.meta_agent,
            provider_factory=self._provider_factory,
            role_registry=self.role_registry,
            injection_manager=self.injection_manager,
            run_event_hub=self.run_event_hub,
            run_control_manager=self.run_control_manager,
            tool_approval_manager=self.tool_approval_manager,
            session_repo=self.session_repo,
            active_run_registry=self.active_run_registry,
            event_log=self.event_log,
            task_repo=self.task_repo,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_intent_repo=self.run_intent_repo,
            run_state_repo=self.run_state_repo,
            background_task_manager=self.background_task_manager,
            background_task_service=self.background_task_service,
            monitor_service=self.monitor_service,
            notification_service=self.notification_service,
            orchestration_settings_service=self.orchestration_settings_service,
            media_asset_service=self.media_asset_service,
            runtime_role_resolver=self.runtime_role_resolver,
            shell_approval_repo=self.shell_approval_repo,
            hook_service=self.hook_service,
        )
        self.monitor_service.bind_action_sink(self.run_service)
        self.session_service: SessionService = SessionService(
            session_repo=self.session_repo,
            task_repo=self.task_repo,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            approval_ticket_repo=self.approval_ticket_repo,
            run_runtime_repo=self.run_runtime_repo,
            token_usage_repo=self.token_usage_repo,
            monitor_repository=self.monitor_repository,
            background_task_repository=self.background_task_repository,
            run_state_repo=self.run_state_repo,
            run_event_hub=self.run_event_hub,
            active_run_registry=self.active_run_registry,
            event_log=self.event_log,
            session_history_marker_repo=self.session_history_marker_repo,
            shared_store=self.shared_store,
            metrics_store=self.metrics_store,
            workspace_manager=self.workspace_manager,
            workspace_service=self.workspace_service,
            external_session_binding_repo=self.external_session_binding_repo,
            role_memory_service=self.role_memory_service,
            subagent_reflection_service=self.subagent_reflection_service,
            role_registry=self.role_registry,
            skill_registry=self.skill_registry,
            mcp_registry=self.mcp_registry,
            orchestration_settings_service=self.orchestration_settings_service,
            media_asset_service=self.media_asset_service,
            get_runtime=lambda: self.runtime,
        )
        self.session_ingress_service = GatewaySessionIngressService(
            run_service=self.run_service,
            run_runtime_repo=self.run_runtime_repo,
        )
        self.gateway_session_service = GatewaySessionService(
            repository=self.gateway_session_repository,
            session_service=self.session_service,
            workspace_service=self.workspace_service,
        )
        self.feishu_inbound_runtime = FeishuInboundRuntime(
            session_service=self.session_service,
            run_service=self.run_service,
            external_session_binding_repo=self.external_session_binding_repo,
            feishu_client=self.feishu_client,
            session_ingress_service=self.session_ingress_service,
        )
        self.feishu_message_pool_service = FeishuMessagePoolService(
            runtime_config_lookup=self.feishu_gateway_service,
            inbound_runtime=self.feishu_inbound_runtime,
            feishu_client=self.feishu_client,
            message_pool_repo=self.feishu_message_pool_repo,
            run_runtime_repo=self.run_runtime_repo,
            event_log=self.event_log,
            external_session_binding_repo=self.external_session_binding_repo,
            automation_queue_repo=self.automation_bound_session_queue_repo,
        )
        self.im_session_command_service = ImSessionCommandService(
            session_service=self.session_service,
            run_service=self.run_service,
            external_session_binding_repo=self.external_session_binding_repo,
            gateway_session_service=self.gateway_session_service,
            feishu_message_pool_service=self.feishu_message_pool_service,
        )
        self.wechat_gateway_service = WeChatGatewayService(
            config_dir=app_config_dir,
            repository=self.wechat_account_repository,
            secret_store=None,
            client=self.wechat_client,
            gateway_session_service=self.gateway_session_service,
            run_service=self.run_service,
            run_event_hub=self.run_event_hub,
            workspace_service=self.workspace_service,
            role_registry=self.role_registry,
            orchestration_settings_service=self.orchestration_settings_service,
            session_service=self.session_service,
            im_tool_service=self.im_tool_service,
            im_session_command_service=self.im_session_command_service,
            inbound_queue_repo=self.wechat_inbound_queue_repo,
            session_ingress_service=self.session_ingress_service,
        )
        self.automation_feishu_binding_service = AutomationFeishuBindingService(
            external_session_binding_repo=self.external_session_binding_repo,
            session_repo=self.session_repo,
            account_lookup=self.feishu_gateway_service,
            runtime_config_lookup=self.feishu_gateway_service,
        )
        self.automation_delivery_service = AutomationDeliveryService(
            repository=self.automation_delivery_repo,
            runtime_config_lookup=self.feishu_gateway_service,
            feishu_client=self.feishu_client,
            run_runtime_repo=self.run_runtime_repo,
            event_log=self.event_log,
            notification_service=self.notification_service,
        )
        self.automation_delivery_worker = AutomationDeliveryWorker(
            delivery_service=self.automation_delivery_service
        )
        self.notification_service = NotificationService(
            run_event_hub=self.run_event_hub,
            get_config=self.notification_config_manager.get_notification_config,
            dispatchers=(
                FeishuNotificationDispatcher(
                    session_repo=self.session_repo,
                    runtime_config_lookup=self.feishu_gateway_service,
                    feishu_client=self.feishu_client,
                    terminal_notification_suppressor=CompositeTerminalNotificationSuppressor(
                        self.feishu_message_pool_service,
                        self.automation_delivery_service,
                    ),
                ),
            ),
        )
        self.run_service._notification_service = self.notification_service
        self.monitor_service.bind_notification_service(self.notification_service)
        self.automation_bound_session_queue_service = (
            AutomationBoundSessionQueueService(
                repository=self.automation_bound_session_queue_repo,
                session_lookup=self.session_service,
                run_service=self.run_service,
                run_runtime_repo=self.run_runtime_repo,
                delivery_service=self.automation_delivery_service,
                runtime_config_lookup=self.feishu_gateway_service,
                feishu_client=self.feishu_client,
                project_repository=self.automation_repo,
                session_ingress_service=self.session_ingress_service,
            )
        )
        self.automation_bound_session_queue_worker = AutomationBoundSessionQueueWorker(
            queue_service=self.automation_bound_session_queue_service
        )
        self.feishu_trigger_handler = FeishuTriggerHandler(
            runtime_config_lookup=self.feishu_gateway_service,
            message_pool_service=self.feishu_message_pool_service,
            im_tool_service=self.im_tool_service,
            im_session_command_service=self.im_session_command_service,
        )
        self.feishu_subscription_service = FeishuSubscriptionService(
            runtime_config_lookup=self.feishu_gateway_service,
            event_handler=self.feishu_trigger_handler,
        )
        self.automation_service: AutomationService = AutomationService(
            repository=self.automation_repo,
            event_repository=self.automation_event_repo,
            session_service=self.session_service,
            run_service=self.run_service,
            feishu_binding_service=self.automation_feishu_binding_service,
            delivery_service=self.automation_delivery_service,
            bound_session_queue_service=self.automation_bound_session_queue_service,
            workspace_service=self.workspace_service,
            session_ingress_service=self.session_ingress_service,
        )
        self.github_trigger_service = GitHubTriggerService(
            config_dir=app_config_dir,
            repository=self.trigger_repository,
            secret_store=self.github_trigger_secret_store,
            github_client=self.github_api_client,
            automation_service=self.automation_service,
            session_service=self.session_service,
            run_service=self.run_service,
            run_runtime_repo=self.run_runtime_repo,
            event_log=self.event_log,
            monitor_service=self.monitor_service,
            session_ingress_service=self.session_ingress_service,
            get_github_config=self.github_config_service.get_github_config,
        )
        self.github_trigger_action_worker = GitHubTriggerActionWorker(
            trigger_service=self.github_trigger_service
        )
        self.automation_scheduler_service: AutomationSchedulerService = (
            AutomationSchedulerService(automation_service=self.automation_service)
        )
        self.config_status_service: ConfigStatusService = ConfigStatusService(
            get_runtime=lambda: self.runtime,
            get_mcp_registry=lambda: self.mcp_registry,
            get_skill_registry=lambda: self.skill_registry,
            get_proxy_status=self.proxy_config_service.get_proxy_status,
        )
        self.model_config_service: ModelConfigService = ModelConfigService(
            config_dir=app_config_dir,
            roles_dir=self.runtime.paths.roles_dir,
            db_path=self.runtime.paths.db_path,
            model_config_manager=self.model_config_manager,
            model_fallback_config_manager=self.model_fallback_config_manager,
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
            get_external_agent_service=lambda: self.external_agent_config_service,
            on_roles_reloaded=self._on_roles_reloaded,
            reload_skill_registry=lambda: (
                self.skills_config_reload_service.reload_skills_config()
            ),
        )
        self.mcp_config_reload_service: McpConfigReloadService = McpConfigReloadService(
            mcp_config_manager=self.mcp_config_manager,
            role_registry=self.role_registry,
            on_mcp_reloaded=self._on_mcp_reloaded,
        )
        self.skills_config_reload_service: SkillsConfigReloadService = (
            SkillsConfigReloadService(
                config_dir=app_config_dir,
                role_registry=self.role_registry,
                on_skill_reloaded=self._on_skill_reloaded,
            )
        )
        self.clawhub_skill_service: ClawHubSkillService = ClawHubSkillService(
            config_dir=app_config_dir,
            on_skill_mutated=self._reload_skills_config,
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
            run_intent_repo=self.run_intent_repo,
            background_task_service=self.background_task_service,
            monitor_service=self.monitor_service,
            workspace_manager=self.workspace_manager,
            media_asset_service=self.media_asset_service,
            computer_runtime=self.computer_runtime,
            role_memory_service=self.role_memory_service,
            subagent_reflection_service=self.subagent_reflection_service,
            tool_registry=self.tool_registry,
            mcp_registry=self.mcp_registry,
            skill_registry=self.skill_registry,
            message_repo=self.message_repo,
            session_history_marker_repo=self.session_history_marker_repo,
            role_registry=self.role_registry,
            get_task_service=get_task_service,
            run_control_manager=self.run_control_manager,
            tool_approval_manager=self.tool_approval_manager,
            tool_approval_policy=self.tool_approval_policy,
            shell_approval_repo=self.shell_approval_repo,
            notification_service=self.notification_service,
            get_task_execution_service=get_task_execution_service,
            token_usage_repo=self.token_usage_repo,
            metric_recorder=self.metric_recorder,
            im_tool_service=self.im_tool_service,
            external_agent_session_manager=self.external_acp_session_manager,
            session_model_profile_lookup=self._session_model_profile_lookup,
            hook_service=self.hook_service,
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
            run_intent_repo=self.run_intent_repo,
            workspace_manager=self.workspace_manager,
            media_asset_service=self.media_asset_service,
            app_config_dir=self.runtime.paths.config_dir,
            prompt_instructions=self.runtime.prompt_instructions.instructions,
            provider_factory=self._provider_factory,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            skill_runtime_service=self.skill_runtime_service,
            mcp_registry=self.mcp_registry,
            injection_manager=self.injection_manager,
            run_control_manager=self.run_control_manager,
            role_memory_service=self.role_memory_service,
            runtime_role_resolver=self.runtime_role_resolver,
        )
        self.task_service = TaskOrchestrationService(
            task_repo=self.task_repo,
            role_registry=self.role_registry,
            agent_repo=self.agent_repo,
            task_execution_service=self.task_execution_service,
            message_repo=self.message_repo,
            session_repo=self.session_repo,
            runtime_role_resolver=self.runtime_role_resolver,
        )

    def _resolve_reflection_model_config(self) -> ModelEndpointConfig | None:
        if self.runtime.default_model_profile is not None:
            return self.runtime.llm_profiles.get(self.runtime.default_model_profile)
        for profile in self.runtime.llm_profiles.values():
            return profile
        return None

    def _resolve_reflection_model_profile_name(self) -> str | None:
        if self.runtime.default_model_profile is not None:
            return self.runtime.default_model_profile
        for profile_name in self.runtime.llm_profiles.keys():
            return profile_name
        return None

    def _resolve_external_agent_model_config(
        self,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> ModelEndpointConfig | None:
        runtime_to_use = self.runtime
        if (
            self._session_model_profile_lookup is not None
            and (override := self._session_model_profile_lookup(request.session_id))
            is not None
        ):
            runtime_to_use = apply_default_model_profile_override(
                runtime=runtime_to_use,
                override=override,
            )
        return resolve_model_profile_config(
            runtime=runtime_to_use,
            profile_name=role.model_profile,
        )

    def _build_subagent_reflection_service(
        self,
    ) -> SubagentReflectionService | None:
        reflection_config = self._resolve_reflection_model_config()
        if reflection_config is None:
            return None
        return SubagentReflectionService(
            config=reflection_config,
            profile_name=self._resolve_reflection_model_profile_name(),
            retry_config=self.runtime.llm_retry,
            message_repo=self.message_repo,
            role_memory_service=self.role_memory_service,
            fallback_middleware=LlmFallbackMiddleware(
                get_fallback_config=lambda: self.runtime.model_fallback,
                get_profiles=lambda: self.runtime.llm_profiles,
            ),
        )

    async def start(self) -> None:
        self.run_service.bind_event_loop(asyncio.get_running_loop())
        self.background_task_service.bind_completion_sink(self.run_service)
        self.wechat_gateway_service.start()
        self.feishu_subscription_service.start()
        self.feishu_message_pool_service.start()
        self.automation_delivery_worker.start()
        self.automation_bound_session_queue_worker.start()
        self.github_trigger_action_worker.start()
        await self.automation_scheduler_service.start()
        return None

    async def stop(self) -> None:
        await self.automation_scheduler_service.stop()
        self.github_trigger_action_worker.stop()
        self.automation_bound_session_queue_worker.stop()
        self.automation_delivery_worker.stop()
        self.feishu_message_pool_service.stop()
        self.feishu_subscription_service.stop()
        self.wechat_gateway_service.stop()
        self.localhost_run_tunnel_service.stop()
        await self.external_acp_session_manager.close()
        await self.background_task_manager.close()
        return None

    def _sanitize_role_registry(self, role_registry: RoleRegistry) -> RoleRegistry:
        sanitized_registry = RoleRegistry()
        for role in role_registry.list_roles():
            consumer = f"interfaces.server.container.role:{role.role_id}"
            sanitized_registry.register(
                role.model_copy(
                    update={
                        "tools": self.tool_registry.resolve_known(
                            role.tools,
                            context=ToolResolutionContext(session_id=""),
                            strict=False,
                            consumer=consumer,
                        ),
                        "mcp_servers": self.mcp_registry.resolve_server_names(
                            role.mcp_servers,
                            strict=False,
                            consumer=consumer,
                        ),
                        "skills": self.skill_registry.resolve_known(
                            role.skills,
                            strict=False,
                            consumer=consumer,
                        ),
                    }
                )
            )
        return sanitized_registry

    def _refresh_coordinator_runtime(self) -> None:
        self._build_runtime_services()
        self.meta_agent.coordinator.role_registry = self.role_registry
        self.meta_agent.coordinator.prompt_builder = RuntimePromptBuilder(
            role_registry=self.role_registry,
            mcp_registry=self.mcp_registry,
            instruction_resolver=PromptInstructionResolver(
                app_config_dir=self.runtime.paths.config_dir,
                instructions=self.runtime.prompt_instructions.instructions,
            ),
        )
        self.meta_agent.coordinator.provider_factory = self._provider_factory
        self.meta_agent.coordinator.task_execution_service = self.task_execution_service

    def _refresh_runtime_dependents(self) -> None:
        self.runtime_role_resolver.replace_role_registry(self.role_registry)
        self.session_service.replace_role_registry(self.role_registry)
        self.session_service.replace_subagent_reflection_service(
            self.subagent_reflection_service
        )
        self.run_service.replace_runtime_dependencies(
            role_registry=self.role_registry,
            provider_factory=self._provider_factory,
            runtime_role_resolver=self.runtime_role_resolver,
        )
        self.feishu_gateway_service.replace_role_registry(self.role_registry)
        self.wechat_gateway_service.replace_role_registry(self.role_registry)

    def _on_runtime_reloaded(self, runtime: RuntimeConfig) -> None:
        self.runtime = runtime
        self.subagent_reflection_service = self._build_subagent_reflection_service()
        self._refresh_coordinator_runtime()
        self._refresh_runtime_dependents()

    def _on_roles_reloaded(self, role_registry: RoleRegistry) -> None:
        self.role_registry = self._sanitize_role_registry(role_registry)
        self.mcp_config_reload_service = McpConfigReloadService(
            mcp_config_manager=self.mcp_config_manager,
            role_registry=self.role_registry,
            on_mcp_reloaded=self._on_mcp_reloaded,
        )
        self.skills_config_reload_service = SkillsConfigReloadService(
            config_dir=self.runtime.paths.config_dir,
            role_registry=self.role_registry,
            on_skill_reloaded=self._on_skill_reloaded,
        )
        self.clawhub_skill_service = ClawHubSkillService(
            config_dir=self.runtime.paths.config_dir,
            on_skill_mutated=self._reload_skills_config,
        )
        self._refresh_coordinator_runtime()
        self._refresh_runtime_dependents()

    def _on_mcp_reloaded(self, mcp_registry: McpRegistry) -> None:
        self.mcp_registry = mcp_registry
        self.mcp_service.replace_registry(mcp_registry)
        self._refresh_coordinator_runtime()

    def _reload_skills_config(self) -> None:
        self.skills_config_reload_service.reload_skills_config()

    def _on_skill_reloaded(self, skill_registry: SkillRegistry) -> None:
        skill_runtime_service = self._build_skill_runtime_service(
            skill_registry=skill_registry
        )
        self.skill_registry = skill_registry
        self.skill_runtime_service = skill_runtime_service
        self._refresh_coordinator_runtime()

    def _on_proxy_reloaded(self, proxy_config: ProxyEnvConfig) -> None:
        sync_proxy_env_to_process_env(proxy_config)
        clear_llm_http_client_cache()
        self._on_mcp_reloaded(self.mcp_config_manager.load_registry())
        self.feishu_subscription_service.reload()
        self.wechat_gateway_service.reload()

    def _reload_mcp_runtime_after_app_env_change(self) -> None:
        try:
            self._on_mcp_reloaded(self.mcp_config_manager.load_registry())
        except Exception as exc:
            LOGGER.warning(
                "Failed to reload MCP runtime after app environment change: %s",
                exc,
            )

    def _reload_skills_runtime_after_app_env_change(self) -> None:
        try:
            self.skills_config_reload_service.reload_skills_config()
        except Exception as exc:
            LOGGER.warning(
                "Failed to reload skills runtime after app environment change: %s",
                exc,
            )

    def _on_app_environment_changed(self, changed_keys: frozenset[str]) -> None:
        self.model_config_service.reload_model_config()
        proxy_related_keys = {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "SSL_VERIFY",
        }
        normalized_keys = {key.upper() for key in changed_keys}
        if normalized_keys.isdisjoint(proxy_related_keys):
            self._reload_mcp_runtime_after_app_env_change()
        else:
            self.proxy_config_service.reload_proxy_config()
        self._reload_skills_runtime_after_app_env_change()

    def _ensure_default_workspace(self) -> None:
        if self.workspace_repo.exists("default"):
            return
        _ = self.workspace_service.create_workspace(
            workspace_id="default",
            root_path=Path.cwd(),
        )

    def _interrupt_transient_background_tasks(self) -> int:
        interrupted = self.background_task_repository.list_interruptible()
        interrupted_ids: list[str] = []
        for record in interrupted:
            if record.pid is None:
                LOGGER.warning(
                    "Persisted background task lost pid before interruption cleanup",
                    extra={"background_task_id": record.background_task_id},
                )
                interrupted_ids.append(record.background_task_id)
                continue
            killed = kill_process_tree_by_pid(record.pid)
            if not killed:
                LOGGER.warning(
                    "Failed to terminate interrupted background task process",
                    extra={
                        "background_task_id": record.background_task_id,
                        "pid": record.pid,
                    },
                )
                continue
            interrupted_ids.append(record.background_task_id)
        return (
            self.background_task_repository.mark_transient_background_tasks_interrupted(
                background_task_ids=tuple(interrupted_ids)
            )
        )

    def _build_skill_runtime_service(
        self,
        *,
        skill_registry: SkillRegistry,
    ) -> SkillRuntimeService:
        skill_runtime_service = SkillRuntimeService(
            skill_registry=skill_registry,
            retrieval_service=self.retrieval_service,
        )
        skill_runtime_service.rebuild_index()
        return skill_runtime_service
