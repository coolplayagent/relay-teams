# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from pydantic_ai._agent_graph import ModelRequestNode

from relay_teams.agents.execution.coordination_agent_builder import (
    build_coordination_agent,
)
from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
)
from relay_teams.agents.execution.prompt_history import (
    PreparedPromptContext as _PreparedPromptContext,
)
from relay_teams.agents.execution.recovery_flow import (
    AttemptRecoveryOutcome as _AttemptRecoveryOutcome,
    FallbackAttemptOutcome as _FallbackAttemptOutcome,
    FallbackAttemptState as _FallbackAttemptState,
    FallbackAttemptStatus as _FallbackAttemptStatus,
    RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE as _RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
    RETRY_SUPERSEDED_TOOL_CALL_MESSAGE as _RETRY_SUPERSEDED_TOOL_CALL_MESSAGE,
)
from relay_teams.agents.execution.session_prompt import SessionPromptMixin
from relay_teams.agents.execution.session_recovery import SessionRecoveryMixin
from relay_teams.agents.execution.session_runtime import SessionRuntimeMixin
from relay_teams.agents.execution.session_support import SessionSupportMixin
from relay_teams.agents.execution.subagent_reflection import SubagentReflectionService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.orchestration.task_contracts import (
    TaskExecutionServiceLike,
    TaskOrchestrationServiceLike,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.computer import ComputerRuntime
from relay_teams.gateway.im.service import ImToolService
from relay_teams.hooks import HookService
from relay_teams.logger import log_event, log_model_stream_chunk
from relay_teams.media import MediaAssetService
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.metrics import MetricRecorder
from relay_teams.monitors import MonitorService
from relay_teams.notifications import NotificationService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.providers.llm_retry import compute_retry_delay_ms
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.model_fallback import (
    DisabledLlmFallbackMiddleware,
    LlmFallbackMiddleware,
)
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.reminders import SystemReminderService
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.runtime.persisted_state import (
    load_or_recover_tool_call_state,
    load_tool_call_state,
)
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.workspace import WorkspaceManager


class AgentLlmSession(
    SessionRuntimeMixin,
    SessionRecoveryMixin,
    SessionSupportMixin,
    SessionPromptMixin,
):
    _user_question_repo: UserQuestionRepository | None = None
    _user_question_manager: UserQuestionManager | None = None

    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        profile_name: str | None,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
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
        media_asset_service: MediaAssetService | None,
        role_memory_service: RoleMemoryService | None,
        subagent_reflection_service: SubagentReflectionService | None,
        conversation_compaction_service: ConversationCompactionService | None,
        conversation_microcompact_service: ConversationMicrocompactService | None,
        tool_registry: ToolRegistry,
        mcp_registry: McpRegistry,
        skill_registry: SkillRegistry,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        message_repo: MessageRepository,
        role_registry: RoleRegistry,
        task_execution_service: TaskExecutionServiceLike,
        task_service: TaskOrchestrationServiceLike,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        user_question_manager: UserQuestionManager | None = None,
        tool_approval_policy: ToolApprovalPolicy,
        notification_service: NotificationService | None = None,
        token_usage_repo: TokenUsageRepository | None = None,
        metric_recorder: MetricRecorder | None = None,
        retry_config: LlmRetryConfig | None = None,
        fallback_middleware: LlmFallbackMiddleware
        | DisabledLlmFallbackMiddleware
        | None = None,
        im_tool_service: ImToolService | None = None,
        computer_runtime: ComputerRuntime | None = None,
        shell_approval_repo: ShellApprovalRepository | None = None,
        hook_service: HookService | None = None,
        reminder_service: SystemReminderService | None = None,
    ) -> None:
        self._config = config
        self._profile_name = (
            profile_name.strip()
            if profile_name is not None and profile_name.strip()
            else None
        )
        self._task_repo = task_repo
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._user_question_repo = user_question_repo
        self._run_runtime_repo = run_runtime_repo
        self._run_intent_repo = run_intent_repo
        self._background_task_service = background_task_service
        self._todo_service = todo_service
        self._monitor_service = monitor_service
        self._workspace_manager = workspace_manager
        self._media_asset_service = media_asset_service
        self._role_memory_service = role_memory_service
        self._subagent_reflection_service = subagent_reflection_service
        self._conversation_compaction_service = conversation_compaction_service
        self._conversation_microcompact_service = conversation_microcompact_service
        self._tool_registry = tool_registry
        self._mcp_registry = mcp_registry
        self._skill_registry = skill_registry
        self._allowed_tools = allowed_tools
        self._allowed_mcp_servers = allowed_mcp_servers
        self._allowed_skills = allowed_skills
        self._role_registry = role_registry
        self._task_execution_service = task_execution_service
        self._task_service = task_service
        self._run_control_manager = run_control_manager
        self._message_repo = message_repo
        self._tool_approval_manager = tool_approval_manager
        self._user_question_manager = user_question_manager
        self._tool_approval_policy = tool_approval_policy
        self._notification_service = notification_service
        self._token_usage_repo = token_usage_repo
        self._metric_recorder = metric_recorder
        self._retry_config = retry_config or LlmRetryConfig()
        self._fallback_middleware = (
            fallback_middleware
            if fallback_middleware is not None
            else DisabledLlmFallbackMiddleware()
        )
        self._im_tool_service = im_tool_service
        self._computer_runtime = computer_runtime
        self._shell_approval_repo = shell_approval_repo
        self._hook_service = hook_service
        self._reminder_service = reminder_service
        self._mcp_tool_context_token_cache: dict[str, int] = {}


__all__ = [
    "AgentLlmSession",
    "_AttemptRecoveryOutcome",
    "_FallbackAttemptOutcome",
    "_FallbackAttemptState",
    "_FallbackAttemptStatus",
    "_PreparedPromptContext",
    "_RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE",
    "_RETRY_SUPERSEDED_TOOL_CALL_MESSAGE",
    "ModelRequestNode",
    "asyncio",
    "build_coordination_agent",
    "compute_retry_delay_ms",
    "load_or_recover_tool_call_state",
    "load_tool_call_state",
    "log_event",
    "log_model_stream_chunk",
]
