# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
from threading import Lock
from typing import TYPE_CHECKING

from relay_teams.external_agents.provider import (
    ExternalAcpProvider,
    ExternalAcpSessionManager,
)
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.media import MediaAssetService
from relay_teams.monitors import MonitorService
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.metrics import MetricRecorder
from relay_teams.notifications import NotificationService
from relay_teams.paths import format_app_config_file_reference
from relay_teams.providers.provider_contracts import (
    LLMProvider,
    MisconfiguredProvider,
)
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.model_fallback import (
    DisabledLlmFallbackMiddleware,
    LlmFallbackMiddleware,
    ProfileCooldownRegistry,
)
from relay_teams.providers.openai_compatible import OpenAICompatibleProvider
from relay_teams.providers.provider_registry import create_default_provider_registry
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.agents.execution.subagent_reflection import SubagentReflectionService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.runtime_config import RuntimeConfig
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.computer import ComputerRuntime
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.reminders import SystemReminderService
from relay_teams.tools.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.workspace import WorkspaceManager
from relay_teams.hooks import HookService

if TYPE_CHECKING:
    from relay_teams.gateway.im.service import ImToolService


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
    user_question_repo: UserQuestionRepository | None,
    run_runtime_repo: RunRuntimeRepository,
    run_intent_repo: RunIntentRepository,
    background_task_service: BackgroundTaskService | None,
    todo_service: TodoService | None = None,
    monitor_service: MonitorService | None = None,
    workspace_manager: WorkspaceManager,
    media_asset_service: MediaAssetService,
    role_memory_service: RoleMemoryService | None = None,
    subagent_reflection_service: SubagentReflectionService | None = None,
    tool_registry: ToolRegistry,
    mcp_registry: McpRegistry,
    skill_registry: SkillRegistry,
    message_repo: MessageRepository,
    session_history_marker_repo: SessionHistoryMarkerRepository,
    role_registry: RoleRegistry,
    get_task_service: Callable[[], TaskOrchestrationService],
    run_control_manager: RunControlManager,
    tool_approval_manager: ToolApprovalManager,
    user_question_manager: UserQuestionManager | None,
    tool_approval_policy: ToolApprovalPolicy,
    notification_service: NotificationService | None,
    get_task_execution_service: Callable[[], TaskExecutionService],
    shell_approval_repo: ShellApprovalRepository | None = None,
    computer_runtime: ComputerRuntime | None = None,
    token_usage_repo: TokenUsageRepository | None = None,
    metric_recorder: MetricRecorder | None = None,
    im_tool_service: ImToolService | None = None,
    external_agent_session_manager: ExternalAcpSessionManager | None = None,
    session_model_profile_lookup: Callable[[str], ModelEndpointConfig | None]
    | None = None,
    hook_service: HookService | None = None,
    reminder_service: SystemReminderService | None = None,
) -> Callable[[RoleDefinition, str | None], LLMProvider]:
    fallback_cooldown_registries: dict[tuple[str, ...], ProfileCooldownRegistry] = {}
    fallback_cooldown_registry_lock = Lock()

    def resolve_fallback_cooldown_registry(
        runtime_to_use: RuntimeConfig,
    ) -> ProfileCooldownRegistry:
        profile_set_key = _build_fallback_profile_set_key(runtime_to_use)
        with fallback_cooldown_registry_lock:
            registry = fallback_cooldown_registries.get(profile_set_key)
            if registry is None:
                registry = ProfileCooldownRegistry()
                fallback_cooldown_registries[profile_set_key] = registry
            return registry

    def build_fallback_middleware(
        runtime_to_use: RuntimeConfig,
    ) -> LlmFallbackMiddleware:
        return LlmFallbackMiddleware(
            get_fallback_config=lambda runtime_to_use=runtime_to_use: (
                runtime_to_use.model_fallback
            ),
            get_profiles=lambda runtime_to_use=runtime_to_use: (
                runtime_to_use.llm_profiles
            ),
            cooldown_registry=resolve_fallback_cooldown_registry(runtime_to_use),
        )

    def provider_factory(
        role: RoleDefinition, session_id: str | None = None
    ) -> LLMProvider:
        if role.bound_agent_id:
            if external_agent_session_manager is None:
                return MisconfiguredProvider(
                    "External ACP agent runtime is not available. "
                    "Reload the server configuration and try again."
                )
            return ExternalAcpProvider(
                role=role,
                session_manager=external_agent_session_manager,
            )
        runtime_to_use = runtime
        session_override: ModelEndpointConfig | None = None
        if (
            session_id is not None
            and session_model_profile_lookup is not None
            and (session_override := session_model_profile_lookup(session_id))
            is not None
        ):
            runtime_to_use = apply_default_model_profile_override(
                runtime=runtime, override=session_override
            )
        config_to_use = resolve_model_profile_config(
            runtime=runtime_to_use,
            profile_name=role.model_profile,
        )
        profile_name_to_use = resolve_model_profile_name(
            runtime=runtime_to_use,
            profile_name=role.model_profile,
        )
        if config_to_use is None:
            return MisconfiguredProvider(
                "No model profile is configured. "
                "Configure at least one profile in "
                f"{format_app_config_file_reference('model.json', config_dir=runtime_to_use.paths.config_dir)}."
            )
        profile_fallback_middleware: (
            LlmFallbackMiddleware | DisabledLlmFallbackMiddleware
        ) = build_fallback_middleware(runtime_to_use)

        provider_registry = create_default_provider_registry(
            openai_compatible_builder=lambda config: OpenAICompatibleProvider(
                config,
                profile_name=profile_name_to_use,
                task_repo=task_repo,
                shared_store=shared_store,
                event_bus=event_log,
                injection_manager=injection_manager,
                run_event_hub=run_event_hub,
                agent_repo=agent_repo,
                approval_ticket_repo=approval_ticket_repo,
                user_question_repo=user_question_repo,
                run_runtime_repo=run_runtime_repo,
                run_intent_repo=run_intent_repo,
                background_task_service=background_task_service,
                todo_service=todo_service,
                monitor_service=monitor_service,
                workspace_manager=workspace_manager,
                media_asset_service=media_asset_service,
                computer_runtime=computer_runtime,
                role_memory_service=role_memory_service,
                subagent_reflection_service=subagent_reflection_service,
                tool_registry=tool_registry,
                mcp_registry=mcp_registry,
                skill_registry=skill_registry,
                allowed_tools=tool_registry.resolve_known(
                    role.tools,
                    context=ToolResolutionContext(session_id=session_id or ""),
                    strict=False,
                    consumer=f"providers.provider_factory.role:{role.role_id}",
                ),
                allowed_mcp_servers=mcp_registry.resolve_server_names(
                    role.mcp_servers,
                    strict=False,
                    consumer=f"providers.provider_factory.role:{role.role_id}",
                ),
                allowed_skills=skill_registry.resolve_known(
                    role.skills,
                    strict=False,
                    consumer=f"providers.provider_factory.role:{role.role_id}",
                ),
                message_repo=message_repo,
                session_history_marker_repo=session_history_marker_repo,
                role_registry=role_registry,
                task_execution_service=get_task_execution_service(),
                task_service=get_task_service(),
                run_control_manager=run_control_manager,
                tool_approval_manager=tool_approval_manager,
                user_question_manager=user_question_manager,
                tool_approval_policy=tool_approval_policy,
                shell_approval_repo=shell_approval_repo,
                notification_service=notification_service,
                token_usage_repo=token_usage_repo,
                metric_recorder=metric_recorder,
                retry_config=runtime_to_use.llm_retry,
                fallback_middleware=profile_fallback_middleware,
                im_tool_service=im_tool_service,
                hook_service=hook_service,
                reminder_service=reminder_service,
            ),
        )
        return provider_registry.create(config_to_use)

    return provider_factory


def _build_fallback_profile_set_key(runtime: RuntimeConfig) -> tuple[str, ...]:
    entries: list[str] = []
    for profile_name, config in sorted(runtime.llm_profiles.items()):
        serialized_config = json.dumps(
            config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = sha256(serialized_config.encode("utf-8")).hexdigest()
        entries.append(f"{profile_name}:{fingerprint}")
    return tuple(entries)


def apply_default_model_profile_override(
    *,
    runtime: RuntimeConfig,
    override: ModelEndpointConfig,
) -> RuntimeConfig:
    next_profiles = dict(runtime.llm_profiles)
    next_profiles["default"] = override
    next_status_profiles = tuple(sorted(next_profiles.keys()))
    return runtime.model_copy(
        update={
            "llm_profiles": next_profiles,
            "default_model_profile": "default",
            "model_status": runtime.model_status.model_copy(
                update={
                    "loaded": True,
                    "profiles": next_status_profiles,
                    "error": None,
                }
            ),
        }
    )


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


def resolve_model_profile_name(
    *,
    runtime: RuntimeConfig,
    profile_name: str,
) -> str | None:
    normalized_name = profile_name.strip()
    if normalized_name == "default":
        return runtime.default_model_profile
    if normalized_name in runtime.llm_profiles:
        return normalized_name
    return runtime.default_model_profile
