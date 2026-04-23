# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolCallPartDelta,
)

from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
)
from relay_teams.agents.execution.event_publishing import EventPublishingService
from relay_teams.agents.execution.failure_reporting import FailureHandlingService
from relay_teams.agents.execution.message_commit import MessageCommitService
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.prompt_history import (
    PreparedPromptContext,
    PromptHistoryService,
)
from relay_teams.agents.execution.recovery_flow import (
    AttemptRecoveryOutcome,
    AttemptRecoveryService,
    FallbackAttemptOutcome,
    FallbackAttemptState,
    FallbackAttemptStatus,
)
from relay_teams.agents.execution.stream_events import StreamEventService
from relay_teams.agents.execution.subagent_reflection import SubagentReflectionService
from relay_teams.agents.execution.tool_args_recovery import ToolArgsRecoveryService
from relay_teams.agents.execution.tool_result_state import ToolResultStateService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.orchestration.task_contracts import (
    TaskExecutionServiceLike,
    TaskOrchestrationServiceLike,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.computer import ComputerActionDescriptor, ComputerRuntime
from relay_teams.gateway.im.service import ImToolService
from relay_teams.hooks import HookService
from relay_teams.media import MediaAssetService, UserPromptContent
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.metrics import MetricRecorder
from relay_teams.monitors import MonitorService
from relay_teams.notifications import NotificationService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.providers.llm_retry import LlmRetryErrorInfo
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.model_fallback import (
    DisabledLlmFallbackMiddleware,
    LlmFallbackDecision,
    LlmFallbackMiddleware,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_models import RunEvent
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
from relay_teams.tools.runtime.persisted_state import PersistedToolCallState
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.workspace import WorkspaceManager


class AgentLlmSessionMixinBase:  # pragma: no cover
    _config: ModelEndpointConfig
    _profile_name: str | None
    _task_repo: TaskRepository
    _shared_store: SharedStateRepository
    _event_bus: EventLog
    _injection_manager: RunInjectionManager
    _run_event_hub: RunEventHub
    _agent_repo: AgentInstanceRepository
    _approval_ticket_repo: ApprovalTicketRepository
    _user_question_repo: UserQuestionRepository | None
    _run_runtime_repo: RunRuntimeRepository
    _run_intent_repo: RunIntentRepository
    _background_task_service: BackgroundTaskService | None
    _todo_service: TodoService | None
    _monitor_service: MonitorService | None
    _workspace_manager: WorkspaceManager
    _media_asset_service: MediaAssetService | None
    _role_memory_service: RoleMemoryService | None
    _subagent_reflection_service: SubagentReflectionService | None
    _conversation_compaction_service: ConversationCompactionService | None
    _conversation_microcompact_service: ConversationMicrocompactService | None
    _tool_registry: ToolRegistry
    _mcp_registry: McpRegistry
    _skill_registry: SkillRegistry
    _allowed_tools: tuple[str, ...]
    _allowed_mcp_servers: tuple[str, ...]
    _allowed_skills: tuple[str, ...]
    _message_repo: MessageRepository
    _role_registry: RoleRegistry
    _task_execution_service: TaskExecutionServiceLike
    _task_service: TaskOrchestrationServiceLike
    _run_control_manager: RunControlManager
    _tool_approval_manager: ToolApprovalManager
    _user_question_manager: UserQuestionManager | None
    _tool_approval_policy: ToolApprovalPolicy
    _notification_service: NotificationService | None
    _token_usage_repo: TokenUsageRepository | None
    _metric_recorder: MetricRecorder | None
    _retry_config: LlmRetryConfig
    _fallback_middleware: LlmFallbackMiddleware | DisabledLlmFallbackMiddleware
    _im_tool_service: ImToolService | None
    _computer_runtime: ComputerRuntime | None
    _shell_approval_repo: ShellApprovalRepository | None
    _hook_service: HookService | None
    _mcp_tool_context_token_cache: dict[str, int]

    async def _generate_async(
        self,
        request: LLMRequest,
        *,
        retry_number: int = 0,
        total_attempts: int | None = None,
        skip_initial_user_prompt_persist: bool = False,
        fallback_state: FallbackAttemptState | None = None,
    ) -> str:
        raise NotImplementedError

    async def _apply_user_prompt_hooks(
        self,
        request: LLMRequest,
    ) -> tuple[LLMRequest, tuple[str, ...]]:
        raise NotImplementedError

    def _persist_hook_system_context_if_needed(
        self,
        *,
        request: LLMRequest,
        contexts: tuple[str, ...],
    ) -> None:
        raise NotImplementedError

    def _persist_user_prompt_if_needed(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: UserPromptContent | None,
    ) -> tuple[list[ModelRequest | ModelResponse], bool]:
        raise NotImplementedError

    def _current_request_prompt_content(
        self,
        request: LLMRequest,
    ) -> UserPromptContent | None:
        raise NotImplementedError

    async def _build_agent_iteration_context(
        self,
        *,
        request: LLMRequest,
        conversation_id: str,
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> tuple[
        PreparedPromptContext,
        list[ModelRequest | ModelResponse],
        str,
        object,
    ]:
        raise NotImplementedError

    async def _build_model_settings(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> OpenAIChatModelSettings:
        raise NotImplementedError

    def _validate_request_input_capabilities(self, request: LLMRequest) -> None:
        raise NotImplementedError

    def _validate_history_input_capabilities(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        raise NotImplementedError

    def _hydrate_history_media_content(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError

    def _provider_history_for_model_turn(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError

    def _provider_history_for_model_turn_details(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        consumed_tool_call_ids: set[str] | None = None,
    ) -> tuple[list[ModelRequest | ModelResponse], tuple[str, ...]]:
        raise NotImplementedError

    def _handle_model_stream_event(
        self,
        *,
        request: LLMRequest,
        stream_event: object,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> bool:
        raise NotImplementedError

    def _apply_streamed_text_fallback(
        self,
        messages: list[ModelRequest | ModelResponse],
        *,
        streamed_text: str,
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError

    def _publish_text_delta_event(
        self,
        *,
        request: LLMRequest,
        text: str,
    ) -> None:
        raise NotImplementedError

    def _extract_text(self, response: object) -> str:
        raise NotImplementedError

    def _drop_duplicate_leading_request(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        new_messages: list[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError

    def _publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        raise NotImplementedError

    def _publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        raise NotImplementedError

    @staticmethod
    def _normalize_tool_call_args_for_replay(
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        raise NotImplementedError

    def _commit_ready_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        raise NotImplementedError

    def _commit_all_safe_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        raise NotImplementedError

    def _has_pending_tool_calls(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        raise NotImplementedError

    def _filter_model_messages(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError

    def _has_tool_side_effect_messages(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        raise NotImplementedError

    def _last_committable_index(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        raise NotImplementedError

    def _tool_result_already_emitted_from_runtime(
        self,
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
    ) -> bool:
        raise NotImplementedError

    def _maybe_enrich_tool_result_payload(
        self,
        *,
        tool_name: str,
        result_payload: JsonValue,
    ) -> JsonValue:
        raise NotImplementedError

    def _to_json(self, obj: object) -> str:
        raise NotImplementedError

    def _to_json_compatible(self, value: object) -> JsonValue:
        raise NotImplementedError

    def _workspace_id(self, request: LLMRequest) -> str:
        raise NotImplementedError

    def _conversation_id(self, request: LLMRequest) -> str:
        raise NotImplementedError

    def _resolve_tool_approval_policy(self, run_id: str) -> ToolApprovalPolicy:
        raise NotImplementedError

    async def _maybe_recover_from_tool_args_parse_failure(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        emitted_text_chunks: list[str],
        published_tool_call_ids: set[str],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        error_message: str,
    ) -> str | None:
        raise NotImplementedError

    def _collect_pending_tool_calls(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> list[tuple[str, str]]:
        raise NotImplementedError

    async def _restore_pending_tool_results_from_state(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
    ) -> tuple[list[ModelRequest | ModelResponse], int]:
        raise NotImplementedError

    def _visible_tool_result_from_state(
        self,
        *,
        state: PersistedToolCallState | None,
        expected_tool_name: str,
    ) -> dict[str, JsonValue] | None:
        raise NotImplementedError

    def _log_provider_request_failed(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
    ) -> None:
        raise NotImplementedError

    def _build_model_api_error_message(self, error: ModelAPIError) -> str:
        raise NotImplementedError

    async def _handle_generate_attempt_failure(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        error_message: str,
        diagnostics_kind: Literal["model_api_error", "generic_exception"],
        retry_number: int,
        total_attempts: int,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        emitted_text_chunks: list[str],
        published_tool_call_ids: set[str],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        fallback_state: FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> AttemptRecoveryOutcome:
        raise NotImplementedError

    def _raise_terminal_model_api_failure(
        self,
        *,
        request: LLMRequest,
        error: ModelAPIError,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        error_message: str,
        fallback_status: FallbackAttemptStatus,
    ) -> None:
        raise NotImplementedError

    def _raise_terminal_generic_failure(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        fallback_status: FallbackAttemptStatus,
    ) -> None:
        raise NotImplementedError

    def _should_retry_after_text_side_effect(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
    ) -> bool:
        raise NotImplementedError

    def _should_retry_request(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> bool:
        raise NotImplementedError

    def _should_resume_after_tool_outcomes(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_tool_outcome_event_emitted: bool,
    ) -> bool:
        raise NotImplementedError

    def _close_pending_tool_calls_for_retry(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> int:
        raise NotImplementedError

    def _log_generate_failure_diagnostics(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        diagnostics_kind: Literal["model_api_error", "generic_exception"],
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        should_retry: bool,
        should_resume_after_tool_outcomes: bool,
        closed_pending_tool_call_count: int,
    ) -> None:
        raise NotImplementedError

    async def _execute_attempt_recovery(
        self,
        *,
        request: LLMRequest,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        should_retry: bool,
        should_resume_after_tool_outcomes: bool,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        fallback_state: FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> AttemptRecoveryOutcome:
        raise NotImplementedError

    async def _resume_after_tool_outcomes(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        fallback_state: FallbackAttemptState,
    ) -> str:
        raise NotImplementedError

    async def _maybe_fallback_after_retry_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_error: LlmRetryErrorInfo,
        retry_number: int,
        total_attempts: int,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        fallback_state: FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> FallbackAttemptOutcome:
        raise NotImplementedError

    def _clone_with_config(
        self,
        *,
        config: ModelEndpointConfig,
        profile_name: str | None,
    ) -> object:
        raise NotImplementedError

    def _handle_fallback_activated(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        decision: LlmFallbackDecision,
    ) -> None:
        raise NotImplementedError

    def _handle_fallback_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
        fallback_state: FallbackAttemptState,
    ) -> None:
        raise NotImplementedError

    def _handle_retry_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
    ) -> None:
        raise NotImplementedError

    def _raise_assistant_run_error(
        self,
        *,
        request: LLMRequest,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        raise NotImplementedError

    def _publish_synthetic_tool_results_for_pending_calls(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
        error_code: str,
        message: str,
    ) -> int:
        raise NotImplementedError

    def _usage_field_int(self, usage_obj: object, field_name: str) -> int:
        raise NotImplementedError

    def _usage_delta_int(
        self,
        *,
        after: object,
        before: object,
        field_name: str,
    ) -> int:
        raise NotImplementedError

    def _usage_detail_int(self, usage_obj: object, detail_name: str) -> int:
        raise NotImplementedError

    def _usage_detail_delta_int(
        self,
        *,
        after: object,
        before: object,
        detail_name: str,
    ) -> int:
        raise NotImplementedError

    def _build_run_event(
        self,
        *,
        request: LLMRequest,
        event_type: str,
        payload: dict[str, object],
    ) -> RunEvent:
        raise NotImplementedError

    async def _close_run_scoped_llm_http_client(
        self,
        *,
        request: LLMRequest,
    ) -> None:
        raise NotImplementedError

    def _computer_payload_from_raw_result(
        self,
        *,
        descriptor: ComputerActionDescriptor,
        raw_result: JsonValue | None,
    ) -> JsonValue:
        raise NotImplementedError

    def _prompt_history_service(self) -> PromptHistoryService:
        raise NotImplementedError

    def _attempt_recovery_service(self) -> AttemptRecoveryService:
        raise NotImplementedError

    def _failure_handling_service(self) -> FailureHandlingService:
        raise NotImplementedError

    def _stream_event_service(self) -> StreamEventService:
        raise NotImplementedError

    def _tool_args_recovery_service(self) -> ToolArgsRecoveryService:
        raise NotImplementedError

    def _event_publishing_service(self) -> EventPublishingService:
        raise NotImplementedError

    def _message_commit_service(self) -> MessageCommitService:
        raise NotImplementedError

    def _tool_result_state_service(self) -> ToolResultStateService:
        raise NotImplementedError
