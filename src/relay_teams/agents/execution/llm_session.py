# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

import asyncio
import json
import logging
from copy import deepcopy
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from enum import StrEnum
from json import dumps
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable

from pydantic_ai._agent_graph import ModelRequestNode
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ModelResponsePart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from relay_teams.providers.llm_retry import (
    LlmRetryErrorInfo,
    LlmRetrySchedule,
    compute_retry_delay_ms,
    extract_retry_error_info,
)
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.model_fallback import (
    DisabledLlmFallbackMiddleware,
    LlmFallbackDecision,
    LlmFallbackMiddleware,
)
from relay_teams.net.llm_client import reset_llm_http_client_cache_entry
from relay_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from relay_teams.agents.execution.tool_call_history import (
    clone_model_request_with_parts,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.assistant_errors import (
    AssistantRunError,
    AssistantRunErrorPayload,
    build_assistant_error_message,
    build_assistant_error_response,
    build_tool_error_result,
)
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from relay_teams.logger import (
    close_model_stream,
    get_logger,
    log_event,
    log_model_output,
    log_model_stream_chunk,
)
from relay_teams.metrics import MetricRecorder
from relay_teams.metrics.adapters import record_session_step, record_token_usage
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.conversation_compaction import (
    build_conversation_compaction_budget,
    ConversationCompactionBudget,
    ConversationCompactionService,
    ConversationTokenEstimator,
    history_has_valid_tool_replay,
    is_replayable_history,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
)
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.execution.coordination_agent_builder import (
    build_coordination_agent,
)
from relay_teams.agents.execution.tool_args_repair import (
    ToolArgsRepairResult,
    repair_tool_args,
)
from relay_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from relay_teams.computer import (
    ComputerActionDescriptor,
    build_computer_tool_payload,
    describe_builtin_tool,
    describe_mcp_tool,
)
from relay_teams.media import (
    InlineMediaContentPart,
    MediaAssetService,
    MediaModality,
    MediaRefContentPart,
    TextContentPart,
    UserPromptContent,
    normalize_user_prompt_content,
    user_prompt_content_key,
    user_prompt_content_to_text,
)
from relay_teams.monitors import MonitorService
from relay_teams.tools.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.runtime import (
    ToolApprovalManager,
    ToolApprovalPolicy,
    ToolDeps,
)
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    ToolExecutionStatus,
    load_tool_call_state,
    load_or_recover_tool_call_state,
)
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.notifications import NotificationService
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.agents.execution.subagent_reflection import SubagentReflectionService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
)
from relay_teams.hooks import (
    HookDecisionType,
    HookEventName,
    HookService,
    PostCompactInput,
    PreCompactInput,
    UserPromptSubmitInput,
)

if TYPE_CHECKING:
    from relay_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from relay_teams.computer import ComputerRuntime
    from relay_teams.roles.role_registry import RoleRegistry
    from relay_teams.gateway.im import ImToolService

LOGGER = get_logger(__name__)
LLM_REQUEST_LIMIT = 500
_ESTIMATED_TOKEN_BYTES = 4
_ESTIMATED_TOKEN_OVERHEAD = 8
_COMPACTION_OUTPUT_RESERVE_TOKENS = 32
_MIN_AVAILABLE_OUTPUT_TOKENS = 1
_BUILTIN_TOOL_CONTEXT_CHARS = 200
_EXTERNAL_TOOL_CONTEXT_CHARS = 600
_SKILL_CONTEXT_CHARS = 800
_MCP_SERVER_CONTEXT_FALLBACK_CHARS = 1_200
_RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE = "tool_call_superseded_by_retry"
_RETRY_SUPERSEDED_TOOL_CALL_MESSAGE = "This tool call was superseded by an automatic model retry before tool execution started."
_RESUME_SUPERSEDED_TOOL_CALL_ERROR_CODE = "tool_call_superseded_by_resume"
_RESUME_SUPERSEDED_TOOL_CALL_MESSAGE = (
    "This tool call was superseded by automatic recovery after a model request failure."
)


def _format_modality_list(modalities: Sequence[str]) -> str:
    normalized = [str(modality or "").strip().lower() for modality in modalities]
    items = [item for item in normalized if item]
    if not items:
        return "media"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


@runtime_checkable
class _PromptContentPersistenceService(Protocol):
    def to_persisted_user_prompt_content(
        self,
        *,
        parts: tuple[
            TextContentPart | MediaRefContentPart | InlineMediaContentPart, ...
        ],
    ) -> UserPromptContent: ...


@runtime_checkable
class _PromptContentHydrationService(Protocol):
    def hydrate_user_prompt_content(
        self, *, content: UserPromptContent
    ) -> UserPromptContent: ...


class _PreparedPromptContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    history: tuple[ModelRequest | ModelResponse, ...]
    system_prompt: str
    budget: ConversationCompactionBudget
    estimated_history_tokens_before_microcompact: int = 0
    estimated_history_tokens_after_microcompact: int = 0
    microcompact_compacted_message_count: int = 0
    microcompact_compacted_part_count: int = 0


class _FallbackAttemptState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hop: int = Field(default=0, ge=0)
    visited_profiles: tuple[str, ...] = ()

    @classmethod
    def initial(cls, profile_name: str | None) -> "_FallbackAttemptState":
        if profile_name is None or not profile_name.strip():
            return cls()
        return cls(visited_profiles=(profile_name.strip(),))

    def with_profile(self, profile_name: str, *, hop: int) -> "_FallbackAttemptState":
        normalized_name = profile_name.strip()
        visited = list(self.visited_profiles)
        if normalized_name and normalized_name not in visited:
            visited.append(normalized_name)
        return self.model_copy(
            update={
                "hop": hop,
                "visited_profiles": tuple(visited),
            }
        )


class _FallbackAttemptStatus(StrEnum):
    SKIPPED = "skipped"
    RECOVERED = "recovered"
    EXHAUSTED = "exhausted"


class _FallbackAttemptOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: _FallbackAttemptStatus
    response: str | None = None

    @classmethod
    def skipped(cls) -> "_FallbackAttemptOutcome":
        return cls(status=_FallbackAttemptStatus.SKIPPED)

    @classmethod
    def exhausted(cls) -> "_FallbackAttemptOutcome":
        return cls(status=_FallbackAttemptStatus.EXHAUSTED)

    @classmethod
    def recovered(cls, response: str) -> "_FallbackAttemptOutcome":
        return cls(
            status=_FallbackAttemptStatus.RECOVERED,
            response=response,
        )


class _AttemptRecoveryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: str | None = None
    fallback_status: _FallbackAttemptStatus = _FallbackAttemptStatus.SKIPPED

    @classmethod
    def no_recovery(cls) -> "_AttemptRecoveryOutcome":
        return cls()

    @classmethod
    def recovered(
        cls,
        response: str,
        *,
        fallback_status: _FallbackAttemptStatus = _FallbackAttemptStatus.SKIPPED,
    ) -> "_AttemptRecoveryOutcome":
        return cls(
            response=response,
            fallback_status=fallback_status,
        )

    @classmethod
    def fallback_exhausted(cls) -> "_AttemptRecoveryOutcome":
        return cls(fallback_status=_FallbackAttemptStatus.EXHAUSTED)


class _AgentRunResult(Protocol):
    @property
    def response(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


class _AgentNodeStream(Protocol):
    def __aiter__(self) -> AsyncIterator[object]: ...

    def stream_text(self, *, delta: bool) -> AsyncIterator[str]: ...

    def usage(self) -> object: ...


class _AgentNodeStreamContext(Protocol):
    async def __aenter__(self) -> _AgentNodeStream: ...

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None: ...


class _StreamableModelRequestNode(Protocol):
    def stream(self, ctx: object) -> _AgentNodeStreamContext: ...


class _AgentRun(Protocol):
    ctx: object
    result: _AgentRunResult | None

    async def __aenter__(self) -> "_AgentRun": ...

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None: ...

    def __aiter__(self) -> "_AgentRun": ...

    async def __anext__(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


class _CoordinationAgent(Protocol):
    def iter(
        self,
        prompt: str | None,
        *,
        deps: ToolDeps,
        message_history: Sequence[ModelRequest | ModelResponse],
        usage_limits: UsageLimits,
    ) -> _AgentRun: ...


def _resolve_allowed_tools(
    tool_registry: object,
    allowed_tools: tuple[str, ...],
    *,
    session_id: str,
) -> tuple[str, ...]:
    if not allowed_tools:
        return ()
    try:
        return cast(ToolRegistry, tool_registry).resolve_names(
            allowed_tools,
            context=ToolResolutionContext(session_id=session_id),
        )
    except AttributeError:
        return allowed_tools


def _display_text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def _object_payload(value: JsonValue | None) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    return {
        str(key): cast(JsonValue, item)
        for key, item in value.items()
        if isinstance(key, str)
    }


def _content_payload(value: JsonValue | None) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    items: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                str(key): cast(JsonValue, element)
                for key, element in item.items()
                if isinstance(key, str)
            }
        )
    return tuple(items)


def _model_step_payload(
    *,
    role_id: str,
    instance_id: str,
    prepared_prompt: _PreparedPromptContext | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "role_id": role_id,
        "instance_id": instance_id,
    }
    if prepared_prompt is None:
        payload.update(
            {
                "microcompact_applied": False,
                "estimated_tokens_before_microcompact": 0,
                "estimated_tokens_after_microcompact": 0,
                "microcompact_compacted_message_count": 0,
                "microcompact_compacted_part_count": 0,
            }
        )
        return payload
    compacted_message_count = prepared_prompt.microcompact_compacted_message_count
    compacted_part_count = prepared_prompt.microcompact_compacted_part_count
    applied = compacted_message_count > 0 or compacted_part_count > 0
    payload.update(
        {
            "microcompact_applied": applied,
            "estimated_tokens_before_microcompact": (
                prepared_prompt.estimated_history_tokens_before_microcompact
            ),
            "estimated_tokens_after_microcompact": (
                prepared_prompt.estimated_history_tokens_after_microcompact
            ),
            "microcompact_compacted_message_count": compacted_message_count,
            "microcompact_compacted_part_count": compacted_part_count,
        }
    )
    return payload


class AgentLlmSession:
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
        role_registry: "RoleRegistry",
        task_execution_service: "TaskExecutionService",
        task_service: TaskOrchestrationService,
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
        im_tool_service: "ImToolService | None" = None,
        computer_runtime: "ComputerRuntime | None" = None,
        shell_approval_repo: ShellApprovalRepository | None = None,
        hook_service: HookService | None = None,
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
        self._mcp_tool_context_token_cache: dict[str, int] = {}

    async def run(self, request: LLMRequest) -> str:
        return await self._generate_async(request)

    async def _generate_async(
        self,
        request: LLMRequest,
        *,
        retry_number: int = 0,
        total_attempts: int | None = None,
        skip_initial_user_prompt_persist: bool = False,
        fallback_state: _FallbackAttemptState | None = None,
    ) -> str:
        resolved_fallback_state = (
            _FallbackAttemptState.initial(getattr(self, "_profile_name", None))
            if fallback_state is None
            else fallback_state
        )
        resolved_workspace_id = request.workspace_id
        resolved_conversation_id = request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )
        total_attempts = total_attempts or (self._retry_config.max_retries + 1)
        agent_system_prompt = request.system_prompt
        hook_service = getattr(self, "_hook_service", None)
        hook_runtime_env = (
            hook_service.get_run_env(request.run_id) if hook_service is not None else {}
        )
        if not skip_initial_user_prompt_persist:
            request, hook_system_contexts = await self._apply_user_prompt_hooks(request)
            if hook_system_contexts:
                self._persist_hook_system_context_if_needed(
                    request=request,
                    contexts=hook_system_contexts,
                )
        self._validate_request_input_capabilities(request)
        if self._metric_recorder is not None:
            record_session_step(
                self._metric_recorder,
                workspace_id=resolved_workspace_id,
                session_id=request.session_id,
                run_id=request.run_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
            )
        allowed_tools = _resolve_allowed_tools(
            self._tool_registry,
            self._allowed_tools,
            session_id=request.session_id,
        )
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.MODEL_STEP_STARTED,
                payload_json=dumps(
                    _model_step_payload(
                        role_id=request.role_id,
                        instance_id=request.instance_id,
                    )
                ),
            )
        )
        try:
            (
                prepared_prompt,
                history,
                agent_system_prompt,
                agent,
            ) = await self._build_agent_iteration_context(
                request=request,
                conversation_id=resolved_conversation_id,
                system_prompt=agent_system_prompt,
                reserve_user_prompt_tokens=(
                    not skip_initial_user_prompt_persist and retry_number == 0
                ),
                allowed_tools=allowed_tools,
                allowed_mcp_servers=self._allowed_mcp_servers,
                allowed_skills=self._allowed_skills,
            )
            deps = ToolDeps(
                task_repo=self._task_repo,
                shared_store=self._shared_store,
                event_bus=self._event_bus,
                message_repo=self._message_repo,
                approval_ticket_repo=self._approval_ticket_repo,
                user_question_repo=self._user_question_repo,
                run_runtime_repo=self._run_runtime_repo,
                injection_manager=self._injection_manager,
                run_event_hub=self._run_event_hub,
                agent_repo=self._agent_repo,
                workspace=self._workspace_manager.resolve(
                    session_id=request.session_id,
                    role_id=request.role_id,
                    instance_id=request.instance_id,
                    workspace_id=resolved_workspace_id,
                    conversation_id=resolved_conversation_id,
                ),
                role_memory=self._role_memory_service,
                media_asset_service=self._media_asset_service,
                computer_runtime=self._computer_runtime,
                background_task_service=self._background_task_service,
                monitor_service=self._monitor_service,
                todo_service=getattr(self, "_todo_service", None),
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                session_id=request.session_id,
                workspace_id=resolved_workspace_id,
                conversation_id=resolved_conversation_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                role_registry=self._role_registry,
                runtime_role_resolver=getattr(
                    self._task_execution_service, "runtime_role_resolver", None
                ),
                mcp_registry=self._mcp_registry,
                task_service=self._task_service,
                task_execution_service=self._task_execution_service,
                run_control_manager=self._run_control_manager,
                tool_approval_manager=self._tool_approval_manager,
                user_question_manager=self._user_question_manager,
                tool_approval_policy=self._resolve_tool_approval_policy(request.run_id),
                shell_approval_repo=self._shell_approval_repo,
                metric_recorder=self._metric_recorder,
                notification_service=self._notification_service,
                im_tool_service=self._im_tool_service,
                hook_service=hook_service,
                hook_runtime_env=hook_runtime_env,
            )
            control_ctx = self._run_control_manager.context(
                run_id=request.run_id,
                instance_id=request.instance_id,
            )

            printed_any = False
            emitted_text_chunks: list[str] = []
            active_retry_number = retry_number
            attempt_text_emitted = False
            attempt_tool_call_event_emitted = False
            attempt_tool_outcome_event_emitted = False
            attempt_messages_committed = False
            published_tool_call_ids: set[str] = set()
            log_event(
                LOGGER,
                logging.DEBUG,
                event="llm.system_prompt.prepared",
                message=f"LLM system prompt prepared\n{agent_system_prompt}",
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "task_id": request.task_id,
                    "length": len(agent_system_prompt),
                },
            )
            log_event(
                LOGGER,
                logging.INFO,
                event="llm.request.started",
                message="LLM request started",
                payload={
                    "model": self._config.model,
                    "base_url": self._config.base_url,
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "task_id": request.task_id,
                },
            )
            if not skip_initial_user_prompt_persist:
                persisted_history, rebuild_context = (
                    self._persist_user_prompt_if_needed(
                        request=request,
                        history=history,
                        content=self._current_request_prompt_content(request),
                    )
                )
                if rebuild_context:
                    (
                        prepared_prompt,
                        history,
                        agent_system_prompt,
                        agent,
                    ) = await self._build_agent_iteration_context(
                        request=request,
                        conversation_id=resolved_conversation_id,
                        system_prompt=agent_system_prompt,
                        reserve_user_prompt_tokens=(retry_number == 0),
                        allowed_tools=allowed_tools,
                        allowed_mcp_servers=self._allowed_mcp_servers,
                        allowed_skills=self._allowed_skills,
                    )
                else:
                    history = persisted_history
            history = self._hydrate_history_media_content(history)
            seen_count = 0
            buffered_messages: list[ModelRequest | ModelResponse] = []
            restarted = False
            result: _AgentRunResult | None = None
            request_level_input_tokens = 0
            request_level_cached_input_tokens = 0
            request_level_output_tokens = 0
            request_level_reasoning_output_tokens = 0
            request_level_requests = 0
            saw_request_level_usage = False
            streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta] = {}
            latest_streamed_text = ""
        except BaseException:
            await self._close_run_scoped_llm_http_client(request=request)
            raise

        try:
            try:
                while True:
                    control_ctx.raise_if_cancelled()
                    restarted = False
                    async with agent.iter(
                        None,
                        deps=deps,
                        message_history=history,
                        usage_limits=UsageLimits(request_limit=LLM_REQUEST_LIMIT),
                    ) as agent_run:
                        async for node in agent_run:
                            control_ctx.raise_if_cancelled()
                            if isinstance(node, ModelRequestNode):
                                streamable_node = cast(
                                    _StreamableModelRequestNode, node
                                )
                                streamed_tool_calls = {}
                                streamed_text_start = len(emitted_text_chunks)
                                usage_before = deepcopy(agent_run.usage())
                                # Stream text chunks from this model response in real-time
                                async with streamable_node.stream(
                                    agent_run.ctx
                                ) as stream:
                                    stream_iter = getattr(stream, "__aiter__", None)
                                    if callable(stream_iter):
                                        text_lengths: dict[int, int] = {}
                                        thinking_lengths: dict[int, int] = {}
                                        started_thinking_parts: set[int] = set()
                                        async for stream_event in stream:
                                            control_ctx.raise_if_cancelled()
                                            text_emitted = self._handle_model_stream_event(
                                                request=request,
                                                stream_event=stream_event,
                                                emitted_text_chunks=emitted_text_chunks,
                                                text_lengths=text_lengths,
                                                thinking_lengths=thinking_lengths,
                                                started_thinking_parts=started_thinking_parts,
                                                streamed_tool_calls=streamed_tool_calls,
                                            )
                                            if text_emitted:
                                                printed_any = True
                                                attempt_text_emitted = True
                                                if active_retry_number > 0:
                                                    active_retry_number = 0
                                    else:
                                        async for text_delta in stream.stream_text(
                                            delta=True
                                        ):
                                            control_ctx.raise_if_cancelled()
                                            if text_delta:
                                                log_model_stream_chunk(
                                                    request.role_id, text_delta
                                                )
                                                printed_any = True
                                                attempt_text_emitted = True
                                                if active_retry_number > 0:
                                                    active_retry_number = 0
                                                emitted_text_chunks.append(text_delta)
                                                self._publish_text_delta_event(
                                                    request=request,
                                                    text=text_delta,
                                                )
                                usage_after = stream.usage()
                                request_level_input_tokens += self._usage_delta_int(
                                    after=usage_after,
                                    before=usage_before,
                                    field_name="input_tokens",
                                )
                                request_level_cached_input_tokens += (
                                    self._usage_delta_int(
                                        after=usage_after,
                                        before=usage_before,
                                        field_name="cache_read_tokens",
                                    )
                                )
                                request_level_output_tokens += self._usage_delta_int(
                                    after=usage_after,
                                    before=usage_before,
                                    field_name="output_tokens",
                                )
                                request_level_reasoning_output_tokens += (
                                    self._usage_detail_delta_int(
                                        after=usage_after,
                                        before=usage_before,
                                        detail_name="reasoning_tokens",
                                    )
                                )
                                request_level_requests += self._usage_delta_int(
                                    after=usage_after,
                                    before=usage_before,
                                    field_name="requests",
                                )
                                saw_request_level_usage = True
                                streamed_node_text = "".join(
                                    emitted_text_chunks[streamed_text_start:]
                                )
                                latest_streamed_text = streamed_node_text
                            else:
                                streamed_node_text = ""

                            # After each node (ModelRequestNode or others like CallToolsNode),
                            # scan for new messages to emit tool call/result events
                            all_new = agent_run.new_messages()
                            new_batch = list(all_new)[seen_count:]
                            new_to_process = self._drop_duplicate_leading_request(
                                history=history,
                                new_messages=new_batch,
                            )
                            new_to_process = self._apply_streamed_text_fallback(
                                new_to_process,
                                streamed_text=streamed_node_text,
                            )
                            if new_to_process:
                                if active_retry_number > 0:
                                    active_retry_number = 0
                                tool_call_events_emitted = (
                                    self._publish_tool_call_events_from_messages(
                                        request=request,
                                        messages=new_to_process,
                                        published_tool_call_ids=published_tool_call_ids,
                                    )
                                )
                                if tool_call_events_emitted:
                                    attempt_tool_call_event_emitted = True
                                self._normalize_tool_call_args_for_replay(
                                    new_to_process
                                )
                                buffered_messages.extend(new_to_process)
                                previous_history_size = len(history)
                                (
                                    history,
                                    buffered_messages,
                                    committed_tool_events_published,
                                    committed_tool_validation_failures,
                                ) = self._commit_ready_messages(
                                    request=request,
                                    history=history,
                                    pending_messages=buffered_messages,
                                )
                                if committed_tool_events_published:
                                    attempt_tool_outcome_event_emitted = True
                                if len(history) > previous_history_size:
                                    attempt_messages_committed = True
                                if committed_tool_validation_failures:
                                    log_event(
                                        LOGGER,
                                        logging.INFO,
                                        event="llm.tool_input_validation.continue_after_failure",
                                        message=(
                                            "Restarting agent iteration after tool input validation failure"
                                        ),
                                        payload={
                                            "role_id": request.role_id,
                                            "instance_id": request.instance_id,
                                        },
                                    )
                                    (
                                        prepared_prompt,
                                        history,
                                        agent_system_prompt,
                                        agent,
                                    ) = await self._build_agent_iteration_context(
                                        request=request,
                                        conversation_id=resolved_conversation_id,
                                        system_prompt=request.system_prompt,
                                        reserve_user_prompt_tokens=False,
                                        allowed_tools=allowed_tools,
                                        allowed_mcp_servers=self._allowed_mcp_servers,
                                        allowed_skills=self._allowed_skills,
                                    )
                                    seen_count = 0
                                    buffered_messages = []
                                    restarted = True
                                    break
                            seen_count += len(new_batch)

                            # Only restart for injections at a safe persistence boundary.
                            if self._has_pending_tool_calls(buffered_messages):
                                continue
                            injections = self._injection_manager.drain_at_boundary(
                                request.run_id, request.instance_id
                            )
                            if injections:
                                for msg in injections:
                                    self._run_event_hub.publish(
                                        RunEvent(
                                            session_id=request.session_id,
                                            run_id=request.run_id,
                                            trace_id=request.trace_id,
                                            task_id=request.task_id,
                                            instance_id=request.instance_id,
                                            role_id=request.role_id,
                                            event_type=RunEventType.INJECTION_APPLIED,
                                            payload_json=msg.model_dump_json(),
                                        )
                                    )
                                    self._message_repo.append_user_prompt_if_missing(
                                        session_id=request.session_id,
                                        workspace_id=resolved_workspace_id,
                                        conversation_id=resolved_conversation_id,
                                        agent_role_id=request.role_id,
                                        instance_id=request.instance_id,
                                        task_id=request.task_id,
                                        trace_id=request.trace_id,
                                        content=msg.content,
                                    )
                                attempt_messages_committed = True
                                # Restart iter() with injected messages appended to committed history
                                (
                                    prepared_prompt,
                                    history,
                                    agent_system_prompt,
                                    agent,
                                ) = await self._build_agent_iteration_context(
                                    request=request,
                                    conversation_id=resolved_conversation_id,
                                    system_prompt=request.system_prompt,
                                    reserve_user_prompt_tokens=False,
                                    allowed_tools=allowed_tools,
                                    allowed_mcp_servers=self._allowed_mcp_servers,
                                    allowed_skills=self._allowed_skills,
                                )
                                seen_count = 0
                                buffered_messages = []
                                restarted = True
                                break  # break inner for-loop, restart while

                    if not restarted:
                        # Normal completion
                        maybe_result = agent_run.result
                        if maybe_result is None:
                            raise RuntimeError(
                                "Model run finished without a result object"
                            )
                        result = maybe_result
                        # Flush any remaining messages (e.g. final tool results)
                        all_new = maybe_result.new_messages()
                        to_save = self._drop_duplicate_leading_request(
                            history=history,
                            new_messages=list(all_new)[seen_count:],
                        )
                        to_save = self._apply_streamed_text_fallback(
                            to_save,
                            streamed_text=latest_streamed_text,
                        )
                        if to_save:
                            tool_call_events_emitted = (
                                self._publish_tool_call_events_from_messages(
                                    request=request,
                                    messages=to_save,
                                    published_tool_call_ids=published_tool_call_ids,
                                )
                            )
                            if tool_call_events_emitted:
                                attempt_tool_call_event_emitted = True
                            self._normalize_tool_call_args_for_replay(to_save)
                            buffered_messages.extend(to_save)
                        previous_history_size = len(history)
                        (
                            history,
                            buffered_messages,
                            committed_tool_events_published,
                            _committed_tool_validation_failures,
                        ) = self._commit_all_safe_messages(
                            request=request,
                            history=history,
                            pending_messages=buffered_messages,
                        )
                        if committed_tool_events_published:
                            attempt_tool_outcome_event_emitted = True
                        if len(history) > previous_history_size:
                            attempt_messages_committed = True
                        # Record and publish token usage
                        usage = maybe_result.usage()
                        input_tokens = request_level_input_tokens
                        cached_input_tokens = request_level_cached_input_tokens
                        output_tokens = request_level_output_tokens
                        reasoning_output_tokens = request_level_reasoning_output_tokens
                        requests = request_level_requests
                        if not saw_request_level_usage:
                            input_tokens = self._usage_field_int(usage, "input_tokens")
                            cached_input_tokens = self._usage_field_int(
                                usage, "cache_read_tokens"
                            )
                            output_tokens = self._usage_field_int(
                                usage, "output_tokens"
                            )
                            reasoning_output_tokens = self._usage_detail_int(
                                usage, "reasoning_tokens"
                            )
                            requests = self._usage_field_int(usage, "requests")
                        tool_calls = self._usage_field_int(usage, "tool_calls")
                        if self._token_usage_repo is not None:
                            self._token_usage_repo.record(
                                session_id=request.session_id,
                                run_id=request.run_id,
                                instance_id=request.instance_id,
                                role_id=request.role_id,
                                input_tokens=input_tokens,
                                cached_input_tokens=cached_input_tokens,
                                output_tokens=output_tokens,
                                reasoning_output_tokens=reasoning_output_tokens,
                                requests=requests,
                                tool_calls=tool_calls,
                            )
                        self._run_event_hub.publish(
                            RunEvent(
                                session_id=request.session_id,
                                run_id=request.run_id,
                                trace_id=request.trace_id,
                                task_id=request.task_id,
                                instance_id=request.instance_id,
                                role_id=request.role_id,
                                event_type=RunEventType.TOKEN_USAGE,
                                payload_json=dumps(
                                    {
                                        "input_tokens": input_tokens,
                                        "cached_input_tokens": cached_input_tokens,
                                        "output_tokens": output_tokens,
                                        "reasoning_output_tokens": reasoning_output_tokens,
                                        "total_tokens": input_tokens + output_tokens,
                                        "requests": requests,
                                        "tool_calls": tool_calls,
                                        "role_id": request.role_id,
                                        "instance_id": request.instance_id,
                                    }
                                ),
                            )
                        )
                        if self._metric_recorder is not None:
                            record_token_usage(
                                self._metric_recorder,
                                workspace_id=resolved_workspace_id,
                                session_id=request.session_id,
                                run_id=request.run_id,
                                instance_id=request.instance_id,
                                role_id=request.role_id,
                                input_tokens=input_tokens,
                                cached_input_tokens=cached_input_tokens,
                                output_tokens=output_tokens,
                            )
                        log_event(
                            LOGGER,
                            logging.INFO,
                            event="llm.token_usage.recorded",
                            message="LLM token usage recorded",
                            payload={
                                "input_tokens": input_tokens,
                                "cached_input_tokens": cached_input_tokens,
                                "output_tokens": output_tokens,
                                "reasoning_output_tokens": reasoning_output_tokens,
                                "requests": requests,
                                "tool_calls": tool_calls,
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            },
                        )
                        break  # done
            except ModelAPIError as exc:
                self._log_provider_request_failed(request=request, error=exc)
                retry_error = extract_retry_error_info(exc)
                error_message = self._build_model_api_error_message(exc)
                recovery_outcome = await self._handle_generate_attempt_failure(
                    request=request,
                    error=exc,
                    retry_error=retry_error,
                    error_message=error_message,
                    diagnostics_kind="model_api_error",
                    retry_number=active_retry_number,
                    total_attempts=total_attempts,
                    history=history,
                    pending_messages=buffered_messages,
                    emitted_text_chunks=emitted_text_chunks,
                    published_tool_call_ids=published_tool_call_ids,
                    streamed_tool_calls=streamed_tool_calls,
                    attempt_text_emitted=attempt_text_emitted or printed_any,
                    attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                    attempt_tool_outcome_event_emitted=(
                        attempt_tool_outcome_event_emitted
                    ),
                    attempt_messages_committed=attempt_messages_committed,
                    fallback_state=resolved_fallback_state,
                    skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
                )
                if recovery_outcome.response is not None:
                    return recovery_outcome.response
                self._raise_terminal_model_api_failure(
                    request=request,
                    error=exc,
                    retry_error=retry_error,
                    retry_number=active_retry_number,
                    total_attempts=total_attempts,
                    error_message=error_message,
                    fallback_status=recovery_outcome.fallback_status,
                )
            except Exception as exc:
                retry_error = extract_retry_error_info(exc)
                error_message = (
                    retry_error.message
                    if retry_error is not None
                    else (str(exc) or exc.__class__.__name__)
                )
                recovery_outcome = await self._handle_generate_attempt_failure(
                    request=request,
                    error=exc,
                    retry_error=retry_error,
                    error_message=error_message,
                    diagnostics_kind="generic_exception",
                    retry_number=active_retry_number,
                    total_attempts=total_attempts,
                    history=history,
                    pending_messages=buffered_messages,
                    emitted_text_chunks=emitted_text_chunks,
                    published_tool_call_ids=published_tool_call_ids,
                    streamed_tool_calls=streamed_tool_calls,
                    attempt_text_emitted=attempt_text_emitted or printed_any,
                    attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                    attempt_tool_outcome_event_emitted=(
                        attempt_tool_outcome_event_emitted
                    ),
                    attempt_messages_committed=attempt_messages_committed,
                    fallback_state=resolved_fallback_state,
                    skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
                )
                if recovery_outcome.response is not None:
                    return recovery_outcome.response
                self._raise_terminal_generic_failure(
                    request=request,
                    error=exc,
                    retry_error=retry_error,
                    retry_number=active_retry_number,
                    total_attempts=total_attempts,
                    fallback_status=recovery_outcome.fallback_status,
                )

            assert result is not None

            if printed_any:
                close_model_stream()

            text = self._extract_text(result.response)
            if not text and emitted_text_chunks:
                text = "".join(emitted_text_chunks)
            elif text and not emitted_text_chunks:
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=request.session_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        instance_id=request.instance_id,
                        role_id=request.role_id,
                        event_type=RunEventType.TEXT_DELTA,
                        payload_json=dumps(
                            {
                                "text": text,
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            }
                        ),
                    )
                )
            if text and not printed_any:
                log_model_output(request.role_id, text)
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.MODEL_STEP_FINISHED,
                    payload_json=dumps(
                        _model_step_payload(
                            role_id=request.role_id,
                            instance_id=request.instance_id,
                            prepared_prompt=prepared_prompt,
                        )
                    ),
                )
            )
            log_event(
                LOGGER,
                logging.INFO,
                event="llm.request.completed",
                message="LLM request completed",
                payload={
                    "model": self._config.model,
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "task_id": request.task_id,
                    "chars": len(text),
                },
            )
            return text
        finally:
            await self._close_run_scoped_llm_http_client(request=request)

    def _publish_model_step_started_event(self, *, request: LLMRequest) -> None:
        self._publish_model_step_started_event(request=request)

    def _publish_model_step_finished_event(self, *, request: LLMRequest) -> None:
        self._publish_model_step_finished_event(request=request)

    async def _handle_retry_scheduled(
        self,
        *,
        request: LLMRequest,
        schedule: LlmRetrySchedule,
    ) -> None:
        payload = {
            "role_id": request.role_id,
            "instance_id": request.instance_id,
            "attempt_number": schedule.next_attempt_number,
            "total_attempts": schedule.total_attempts,
            "retry_in_ms": schedule.delay_ms,
            "error_code": schedule.error.error_code or "",
            "error_message": schedule.error.message,
            "status_code": schedule.error.status_code,
        }
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.LLM_RETRY_SCHEDULED,
                payload_json=dumps(payload),
            )
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.retrying",
            message="Scheduling LLM request retry",
            payload={
                "run_id": request.run_id,
                "task_id": request.task_id,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "retry_number": schedule.retry_number,
                "next_attempt_number": schedule.next_attempt_number,
                "total_attempts": schedule.total_attempts,
                "delay_ms": schedule.delay_ms,
                "status_code": schedule.error.status_code,
                "error_code": schedule.error.error_code,
                "transport_error": schedule.error.transport_error,
                "timeout_error": schedule.error.timeout_error,
            },
        )

    def _log_provider_request_failed(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
    ) -> None:
        log_event(
            LOGGER,
            logging.ERROR,
            event="llm.request.failed",
            message="LLM provider request failed",
            payload={
                "model": self._config.model,
                "base_url": self._config.base_url,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
            exc_info=error,
        )

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
        fallback_state: _FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> _AttemptRecoveryOutcome:
        """Handle recovery paths for a failed `_generate_async()` attempt.

        This method centralizes the shared failure flow used by both
        `ModelAPIError` and generic exception branches. It tries, in order, to
        recover from tool-args parse failures, emit diagnostics, then execute
        retry, resume, or fallback recovery when those paths are allowed.

        Returns:
            The recovery result for this failed attempt, including whether a
            fallback path was exhausted and already published terminal events.
        """
        recovered = await self._maybe_recover_from_tool_args_parse_failure(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            emitted_text_chunks=emitted_text_chunks,
            published_tool_call_ids=published_tool_call_ids,
            streamed_tool_calls=streamed_tool_calls,
            error_message=error_message,
        )
        if recovered is not None:
            return _AttemptRecoveryOutcome.recovered(recovered)
        should_retry = self._should_retry_request(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
        )
        should_resume_after_tool_outcomes = self._should_resume_after_tool_outcomes(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
        )
        closed_pending_tool_call_count = 0
        if should_retry:
            closed_pending_tool_call_count = self._close_pending_tool_calls_for_retry(
                request=request,
                pending_messages=pending_messages,
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
                attempt_messages_committed=attempt_messages_committed,
            )
        self._log_generate_failure_diagnostics(
            request=request,
            error=error,
            retry_error=retry_error,
            diagnostics_kind=diagnostics_kind,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
            attempt_messages_committed=attempt_messages_committed,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=(should_resume_after_tool_outcomes),
            closed_pending_tool_call_count=closed_pending_tool_call_count,
        )
        return await self._execute_attempt_recovery(
            request=request,
            retry_error=retry_error,
            retry_number=retry_number,
            total_attempts=total_attempts,
            history=history,
            pending_messages=pending_messages,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=(should_resume_after_tool_outcomes),
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
            attempt_messages_committed=attempt_messages_committed,
            fallback_state=fallback_state,
            skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
        )

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
        fallback_state: _FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> _AttemptRecoveryOutcome:
        if should_retry:
            await self._reset_cached_transport_for_retry(
                request=request,
                retry_error=retry_error,
            )
            resolved_retry_error = retry_error
            assert resolved_retry_error is not None
            next_retry_number = retry_number + 1
            delay_ms = compute_retry_delay_ms(
                config=self._retry_config,
                retry_number=next_retry_number,
                retry_after_ms=resolved_retry_error.retry_after_ms,
            )
            await self._handle_retry_scheduled(
                request=request,
                schedule=LlmRetrySchedule(
                    retry_number=next_retry_number,
                    next_attempt_number=next_retry_number + 1,
                    total_attempts=total_attempts,
                    delay_ms=delay_ms,
                    error=resolved_retry_error,
                ),
            )
            await asyncio.sleep(delay_ms / 1000)
            return _AttemptRecoveryOutcome.recovered(
                await self._generate_async(
                    request,
                    retry_number=next_retry_number,
                    total_attempts=total_attempts,
                    fallback_state=fallback_state,
                )
            )
        if should_resume_after_tool_outcomes:
            await self._reset_cached_transport_for_retry(
                request=request,
                retry_error=retry_error,
            )
            return _AttemptRecoveryOutcome.recovered(
                await self._resume_after_tool_outcomes(
                    request=request,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    history=history,
                    pending_messages=pending_messages,
                    fallback_state=fallback_state,
                )
            )
        if retry_error is not None:
            fallback_outcome = await self._maybe_fallback_after_retry_exhausted(
                request=request,
                retry_number=retry_number,
                total_attempts=total_attempts,
                retry_error=retry_error,
                fallback_state=fallback_state,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
                attempt_messages_committed=attempt_messages_committed,
                skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
            )
            if fallback_outcome.response is not None:
                return _AttemptRecoveryOutcome.recovered(
                    fallback_outcome.response,
                    fallback_status=fallback_outcome.status,
                )
            if fallback_outcome.status == _FallbackAttemptStatus.EXHAUSTED:
                return _AttemptRecoveryOutcome.fallback_exhausted()
        return _AttemptRecoveryOutcome.no_recovery()

    async def _reset_cached_transport_for_retry(
        self,
        *,
        request: LLMRequest,
        retry_error: LlmRetryErrorInfo | None,
    ) -> None:
        if retry_error is None or not retry_error.transport_error:
            return
        await reset_llm_http_client_cache_entry(
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            cache_scope=request.run_id,
        )

    async def _close_run_scoped_llm_http_client(
        self,
        *,
        request: LLMRequest,
    ) -> None:
        await reset_llm_http_client_cache_entry(
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            cache_scope=request.run_id,
        )

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
        if diagnostics_kind == "model_api_error":
            assert isinstance(error, ModelAPIError)
            event = "llm.request.model_api_error.diagnostics"
            message = "ModelAPIError retry diagnostics"
            payload = self._model_api_error_diagnostics_payload(
                error=error,
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_call_event_emitted=(attempt_tool_call_event_emitted),
                attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
                attempt_messages_committed=attempt_messages_committed,
                should_retry=should_retry,
                should_resume_after_tool_outcomes=(should_resume_after_tool_outcomes),
                closed_pending_tool_call_count=closed_pending_tool_call_count,
            )
        else:
            event = "llm.request.exception.diagnostics"
            message = "Unhandled exception retry diagnostics"
            payload = self._exception_retry_diagnostics_payload(
                error=error,
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_call_event_emitted=(attempt_tool_call_event_emitted),
                attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
                attempt_messages_committed=attempt_messages_committed,
                should_retry=should_retry,
                should_resume_after_tool_outcomes=(should_resume_after_tool_outcomes),
                closed_pending_tool_call_count=closed_pending_tool_call_count,
            )
        log_event(
            LOGGER,
            logging.ERROR,
            event=event,
            message=message,
            payload=cast(
                dict[str, JsonValue],
                self._to_json_compatible(payload),
            ),
        )

    def _publish_synthetic_tool_results_for_pending_calls(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
        error_code: str,
        message: str,
    ) -> int:
        pending_tool_calls = self._collect_pending_tool_calls(pending_messages)
        if not pending_tool_calls:
            return 0
        synthetic_request = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    content=build_tool_error_result(
                        error_code=error_code,
                        message=message,
                    ),
                )
                for tool_call_id, tool_name in pending_tool_calls
            ]
        )
        self._publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=[synthetic_request],
        )
        return len(pending_tool_calls)

    def _close_pending_tool_calls_for_retry(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> int:
        if (
            not attempt_tool_call_event_emitted
            or attempt_tool_outcome_event_emitted
            or attempt_messages_committed
        ):
            return 0
        return self._publish_synthetic_tool_results_for_pending_calls(
            request=request,
            pending_messages=pending_messages,
            error_code=_RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
            message=_RETRY_SUPERSEDED_TOOL_CALL_MESSAGE,
        )

    def _should_retry_request(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> bool:
        allow_after_text = self._should_retry_after_text_side_effect(
            retry_error=retry_error
        )
        return (
            retry_error is not None
            and retry_error.retryable
            and self._retry_config.enabled
            and retry_number < self._retry_config.max_retries
            and (not attempt_text_emitted or allow_after_text)
            and not attempt_tool_outcome_event_emitted
            and not attempt_messages_committed
        )

    def _should_resume_after_tool_outcomes(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_tool_outcome_event_emitted: bool,
    ) -> bool:
        return (
            retry_error is not None
            and retry_error.retryable
            and self._retry_config.enabled
            and retry_number < self._retry_config.max_retries
            and attempt_tool_outcome_event_emitted
        )

    async def _resume_after_tool_outcomes(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        fallback_state: _FallbackAttemptState,
    ) -> str:
        next_retry_number = retry_number + 1
        recovered_pending_messages, recovered_tool_result_count = (
            self._restore_pending_tool_results_from_state(
                request=request,
                pending_messages=pending_messages,
            )
        )
        (
            next_history,
            remaining_pending_messages,
            _committed_tool_events_published,
            _committed_tool_validation_failures,
        ) = self._commit_all_safe_messages(
            request=request,
            history=history,
            pending_messages=recovered_pending_messages,
        )
        closed_pending_tool_call_count = (
            self._publish_synthetic_tool_results_for_pending_calls(
                request=request,
                pending_messages=remaining_pending_messages,
                error_code=_RESUME_SUPERSEDED_TOOL_CALL_ERROR_CODE,
                message=_RESUME_SUPERSEDED_TOOL_CALL_MESSAGE,
            )
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.resuming_after_tool_outcomes",
            message=(
                "Resuming LLM request from the latest committed tool outcomes "
                "after a retryable provider failure"
            ),
            payload={
                "run_id": request.run_id,
                "task_id": request.task_id,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "retry_number": retry_number,
                "next_attempt_number": next_retry_number + 1,
                "history_message_count": len(next_history),
                "dropped_pending_message_count": len(remaining_pending_messages),
                "recovered_tool_result_count": recovered_tool_result_count,
                "closed_pending_tool_call_count": closed_pending_tool_call_count,
            },
        )
        return await self._generate_async(
            request,
            retry_number=next_retry_number,
            total_attempts=total_attempts,
            skip_initial_user_prompt_persist=True,
            fallback_state=fallback_state,
        )

    def _raise_terminal_model_api_failure(
        self,
        *,
        request: LLMRequest,
        error: ModelAPIError,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        error_message: str,
        fallback_status: _FallbackAttemptStatus,
    ) -> None:
        if retry_error is not None and retry_error.retryable:
            if (
                fallback_status != _FallbackAttemptStatus.EXHAUSTED
                and self._retry_config.enabled
                and retry_number >= self._retry_config.max_retries
            ):
                self._handle_retry_exhausted(
                    request=request,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    error=retry_error,
                )
            self._raise_assistant_run_error(
                request=request,
                error_code=retry_error.error_code,
                error_message=error_message,
            )
        self._raise_assistant_run_error(
            request=request,
            error_code=(
                retry_error.error_code
                if retry_error is not None
                else getattr(error, "model_name", None)
            ),
            error_message=error_message,
        )

    def _raise_terminal_generic_failure(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        fallback_status: _FallbackAttemptStatus,
    ) -> None:
        if retry_error is not None:
            self._log_provider_request_failed(request=request, error=error)
            if retry_error.retryable and (
                fallback_status != _FallbackAttemptStatus.EXHAUSTED
                and self._retry_config.enabled
                and retry_number >= self._retry_config.max_retries
            ):
                self._handle_retry_exhausted(
                    request=request,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    error=retry_error,
                )
            self._raise_assistant_run_error(
                request=request,
                error_code=retry_error.error_code,
                error_message=retry_error.message,
            )
        self._raise_assistant_run_error(
            request=request,
            error_code="internal_execution_error",
            error_message=str(error) or error.__class__.__name__,
        )

    def _should_retry_after_text_side_effect(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
    ) -> bool:
        if retry_error is None or not retry_error.retryable:
            return False
        if retry_error.transport_error:
            return True
        status_code = retry_error.status_code
        return status_code is not None and (status_code == 429 or status_code >= 500)

    def _can_attempt_fallback(
        self,
        *,
        retry_error: LlmRetryErrorInfo,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> bool:
        retries_exhausted = (
            not self._retry_config.enabled
            or not retry_error.retryable
            or retry_number >= self._retry_config.max_retries
        )
        return (
            retry_error.rate_limited
            and retries_exhausted
            and not attempt_text_emitted
            and not attempt_tool_call_event_emitted
            and not attempt_tool_outcome_event_emitted
            and not attempt_messages_committed
        )

    async def _maybe_fallback_after_retry_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        retry_error: LlmRetryErrorInfo,
        fallback_state: _FallbackAttemptState,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        skip_initial_user_prompt_persist: bool,
    ) -> _FallbackAttemptOutcome:
        if not self._can_attempt_fallback(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
            attempt_messages_committed=attempt_messages_committed,
        ):
            return _FallbackAttemptOutcome.skipped()
        fallback_middleware = getattr(
            self,
            "_fallback_middleware",
            DisabledLlmFallbackMiddleware(),
        )
        if not fallback_middleware.has_enabled_policy(self._config):
            return _FallbackAttemptOutcome.skipped()
        decision = fallback_middleware.select_fallback(
            current_profile_name=self._profile_name,
            current_config=self._config,
            error=retry_error,
            visited_profiles=fallback_state.visited_profiles,
            hop=fallback_state.hop,
        )
        if decision is None:
            self._handle_fallback_exhausted(
                request=request,
                retry_number=retry_number,
                total_attempts=total_attempts,
                error=retry_error,
                fallback_state=fallback_state,
            )
            return _FallbackAttemptOutcome.exhausted()
        self._handle_fallback_activated(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            decision=decision,
        )
        next_session = self._clone_with_config(
            config=decision.target_config,
            profile_name=decision.to_profile_name,
        )
        next_fallback_state = fallback_state.with_profile(
            decision.to_profile_name,
            hop=decision.hop,
        )
        return _FallbackAttemptOutcome.recovered(
            await next_session._generate_async(
                request,
                retry_number=0,
                total_attempts=None,
                skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
                fallback_state=next_fallback_state,
            )
        )

    def _clone_with_config(
        self,
        *,
        config: ModelEndpointConfig,
        profile_name: str | None,
    ) -> "AgentLlmSession":
        return AgentLlmSession(
            config=config,
            profile_name=profile_name,
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            approval_ticket_repo=self._approval_ticket_repo,
            user_question_repo=self._user_question_repo,
            run_runtime_repo=self._run_runtime_repo,
            run_intent_repo=self._run_intent_repo,
            background_task_service=self._background_task_service,
            todo_service=getattr(self, "_todo_service", None),
            monitor_service=self._monitor_service,
            workspace_manager=self._workspace_manager,
            media_asset_service=self._media_asset_service,
            role_memory_service=self._role_memory_service,
            subagent_reflection_service=(
                self._subagent_reflection_service.with_config(
                    config,
                    profile_name=profile_name,
                )
                if self._subagent_reflection_service is not None
                else None
            ),
            conversation_compaction_service=(
                self._conversation_compaction_service.with_config(
                    config,
                    profile_name=profile_name,
                )
                if self._conversation_compaction_service is not None
                else None
            ),
            conversation_microcompact_service=self._conversation_microcompact_service,
            tool_registry=self._tool_registry,
            mcp_registry=self._mcp_registry,
            skill_registry=self._skill_registry,
            allowed_tools=self._allowed_tools,
            allowed_mcp_servers=self._allowed_mcp_servers,
            allowed_skills=self._allowed_skills,
            message_repo=self._message_repo,
            role_registry=self._role_registry,
            task_execution_service=self._task_execution_service,
            task_service=self._task_service,
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            user_question_manager=self._user_question_manager,
            tool_approval_policy=self._tool_approval_policy,
            notification_service=self._notification_service,
            token_usage_repo=self._token_usage_repo,
            metric_recorder=self._metric_recorder,
            retry_config=self._retry_config,
            fallback_middleware=getattr(
                self,
                "_fallback_middleware",
                DisabledLlmFallbackMiddleware(),
            ),
            im_tool_service=self._im_tool_service,
            computer_runtime=self._computer_runtime,
            shell_approval_repo=self._shell_approval_repo,
        )

    def _build_recoverable_pause_error(
        self,
        *,
        request: LLMRequest,
        error: LlmRetryErrorInfo,
        retry_number: int,
        total_attempts: int,
        error_message: str | None = None,
    ) -> RecoverableRunPauseError:
        return RecoverableRunPauseError(
            RecoverableRunPausePayload.from_request(
                request=request,
                error=error,
                retries_used=retry_number,
                total_attempts=total_attempts,
                error_message=error_message,
            )
        )

    def _raise_assistant_run_error(
        self,
        *,
        request: LLMRequest,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        assistant_message = build_assistant_error_message(
            error_code=error_code,
            error_message=error_message,
        )
        self._message_repo.prune_conversation_history_to_safe_boundary(
            self._conversation_id(request)
        )
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[build_assistant_error_response(assistant_message)],
        )
        self._publish_text_delta_event(request=request, text=assistant_message)
        raise AssistantRunError(
            AssistantRunErrorPayload(
                trace_id=request.trace_id,
                session_id=request.session_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                conversation_id=self._conversation_id(request),
                assistant_message=assistant_message,
                error_code=str(error_code or ""),
                error_message=str(error_message or ""),
            )
        )

    def _handle_retry_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
    ) -> None:
        payload = {
            "role_id": request.role_id,
            "instance_id": request.instance_id,
            "attempt_number": retry_number + 1,
            "total_attempts": total_attempts,
            "error_code": error.error_code or "",
            "error_message": error.message,
            "status_code": error.status_code,
        }
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.LLM_RETRY_EXHAUSTED,
                payload_json=dumps(payload),
            )
        )
        log_event(
            LOGGER,
            logging.ERROR,
            event="llm.request.retry_exhausted",
            message="LLM request retries exhausted",
            payload={
                "run_id": request.run_id,
                "task_id": request.task_id,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "retries_used": retry_number,
                "attempt_number": retry_number + 1,
                "total_attempts": total_attempts,
                "status_code": error.status_code,
                "error_code": error.error_code,
            },
        )

    def _handle_fallback_activated(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        decision: LlmFallbackDecision,
    ) -> None:
        payload = {
            "role_id": request.role_id,
            "instance_id": request.instance_id,
            "attempt_number": retry_number + 1,
            "total_attempts": total_attempts,
            "strategy_id": decision.policy_id,
            "from_profile_id": decision.from_profile_name,
            "to_profile_id": decision.to_profile_name,
            "from_provider": decision.from_provider.value,
            "to_provider": decision.to_provider.value,
            "from_model": decision.from_model,
            "to_model": decision.to_model,
            "hop": decision.hop,
            "reason": decision.reason,
        }
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.LLM_FALLBACK_ACTIVATED,
                payload_json=dumps(payload),
            )
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.fallback_activated",
            message="LLM request fallback activated after rate limit exhaustion",
            payload=payload,
        )

    def _handle_fallback_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
        fallback_state: _FallbackAttemptState,
    ) -> None:
        payload = {
            "role_id": request.role_id,
            "instance_id": request.instance_id,
            "attempt_number": retry_number + 1,
            "total_attempts": total_attempts,
            "from_profile_id": self._profile_name or "",
            "from_provider": self._config.provider.value,
            "from_model": self._config.model,
            "hop": fallback_state.hop,
            "visited_profiles": list(fallback_state.visited_profiles),
            "error_code": error.error_code or "",
            "error_message": error.message,
            "status_code": error.status_code,
        }
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.LLM_FALLBACK_EXHAUSTED,
                payload_json=dumps(payload),
            )
        )
        log_event(
            LOGGER,
            logging.ERROR,
            event="llm.request.fallback_exhausted",
            message="No fallback candidate succeeded after LLM rate limit exhaustion",
            payload=payload,
        )

    def _usage_field_int(self, usage_obj: object, field_name: str) -> int:
        value = getattr(usage_obj, field_name, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        return 0

    def _usage_delta_int(
        self, *, after: object, before: object, field_name: str
    ) -> int:
        after_value = self._usage_field_int(after, field_name)
        before_value = self._usage_field_int(before, field_name)
        delta = after_value - before_value
        return delta if delta > 0 else 0

    def _usage_detail_int(self, usage_obj: object, detail_name: str) -> int:
        details = getattr(usage_obj, "details", {})
        if not isinstance(details, dict):
            return 0
        value = details.get(detail_name, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        return 0

    def _usage_detail_delta_int(
        self, *, after: object, before: object, detail_name: str
    ) -> int:
        after_value = self._usage_detail_int(after, detail_name)
        before_value = self._usage_detail_int(before, detail_name)
        delta = after_value - before_value
        return delta if delta > 0 else 0

    def _build_model_api_error_message(self, error: ModelAPIError) -> str:
        chain = self._exception_chain(error)
        if self._is_proxy_auth_failure(chain):
            return (
                f"{error.message} Proxy authentication failed (HTTP 407). "
                "Check HTTP_PROXY/HTTPS_PROXY credentials or set NO_PROXY for the model endpoint."
            )
        if self._is_connect_timeout(chain):
            return (
                f"{error.message} Connection to the model endpoint timed out. "
                "Check base_url, proxy/NO_PROXY settings, network reachability, "
                "or increase connect_timeout_seconds in the model profile."
            )

        root_message = self._deepest_distinct_exception_message(
            chain=chain,
            primary_message=error.message,
        )
        if root_message is None:
            return error.message
        return f"{error.message} Root cause: {root_message}"

    def _model_api_error_diagnostics_payload(
        self,
        *,
        error: ModelAPIError,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        should_retry: bool,
        should_resume_after_tool_outcomes: bool,
        closed_pending_tool_call_count: int,
    ) -> dict[str, object]:
        chain = self._exception_chain(error)
        response = getattr(error, "response", None)
        response_headers = getattr(response, "headers", None)
        direct_headers = getattr(error, "headers", None)
        return {
            "error_type": error.__class__.__name__,
            "message": str(error),
            "error_message": getattr(error, "message", str(error)),
            "model_name": getattr(error, "model_name", None),
            "status_code": getattr(error, "status_code", None),
            "code": getattr(error, "code", None),
            "body": self._diagnostic_value(getattr(error, "body", None)),
            "headers": self._diagnostic_headers(direct_headers),
            "response_headers": self._diagnostic_headers(response_headers),
            "exception_chain": [
                self._exception_diagnostic_item(item) for item in chain
            ],
            "retry_error": (
                retry_error.model_dump(mode="json") if retry_error is not None else None
            ),
            "retry_number": retry_number,
            "max_retries": self._retry_config.max_retries,
            "retry_enabled": self._retry_config.enabled,
            "attempt_text_emitted": attempt_text_emitted,
            "attempt_tool_call_event_emitted": attempt_tool_call_event_emitted,
            "attempt_tool_outcome_event_emitted": attempt_tool_outcome_event_emitted,
            "tool_event_state": self._tool_event_state(
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            ),
            "attempt_messages_committed": attempt_messages_committed,
            "should_retry": should_retry,
            "should_resume_after_tool_outcomes": should_resume_after_tool_outcomes,
            "closed_pending_tool_call_count": closed_pending_tool_call_count,
            "retry_blockers": self._retry_blockers(
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
                attempt_messages_committed=attempt_messages_committed,
            ),
        }

    def _exception_retry_diagnostics_payload(
        self,
        *,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        should_retry: bool,
        should_resume_after_tool_outcomes: bool,
        closed_pending_tool_call_count: int,
    ) -> dict[str, object]:
        chain = self._exception_chain(error)
        return {
            "error_type": error.__class__.__name__,
            "message": str(error),
            "args": [self._diagnostic_value(item) for item in error.args],
            "exception_chain": [
                self._exception_diagnostic_item(item) for item in chain
            ],
            "retry_error": (
                retry_error.model_dump(mode="json") if retry_error is not None else None
            ),
            "retry_number": retry_number,
            "max_retries": self._retry_config.max_retries,
            "retry_enabled": self._retry_config.enabled,
            "attempt_text_emitted": attempt_text_emitted,
            "attempt_tool_call_event_emitted": attempt_tool_call_event_emitted,
            "attempt_tool_outcome_event_emitted": attempt_tool_outcome_event_emitted,
            "tool_event_state": self._tool_event_state(
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            ),
            "attempt_messages_committed": attempt_messages_committed,
            "should_retry": should_retry,
            "should_resume_after_tool_outcomes": should_resume_after_tool_outcomes,
            "closed_pending_tool_call_count": closed_pending_tool_call_count,
            "retry_blockers": self._retry_blockers(
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_outcome_event_emitted=(attempt_tool_outcome_event_emitted),
                attempt_messages_committed=attempt_messages_committed,
            ),
        }

    def _exception_chain(self, error: BaseException) -> tuple[BaseException, ...]:
        chain: list[BaseException] = []
        seen_ids: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen_ids:
            chain.append(current)
            seen_ids.add(id(current))
            if current.__cause__ is not None:
                current = current.__cause__
                continue
            if current.__suppress_context__:
                break
            current = current.__context__
        return tuple(chain)

    def _is_proxy_auth_failure(
        self,
        chain: Sequence[BaseException],
    ) -> bool:
        for error in chain:
            message = str(error).strip().lower()
            if "407 proxy authentication required" in message:
                return True
        return False

    def _is_connect_timeout(
        self,
        chain: Sequence[BaseException],
    ) -> bool:
        for error in chain:
            if error.__class__.__name__ == "ConnectTimeout":
                return True
        return False

    def _deepest_distinct_exception_message(
        self,
        *,
        chain: Sequence[BaseException],
        primary_message: str,
    ) -> str | None:
        normalized_primary = primary_message.strip()
        for error in reversed(chain):
            message = str(error).strip()
            if not message or message == normalized_primary:
                continue
            return message
        return None

    def _exception_diagnostic_item(self, error: BaseException) -> dict[str, object]:
        response = getattr(error, "response", None)
        return {
            "type": error.__class__.__name__,
            "message": str(error),
            "status_code": getattr(error, "status_code", None),
            "code": getattr(error, "code", None),
            "body": self._diagnostic_value(getattr(error, "body", None)),
            "response_headers": self._diagnostic_headers(
                getattr(response, "headers", None)
            ),
        }

    def _diagnostic_value(self, value: object) -> object:
        compatible = self._to_json_compatible(value)
        serialized = json.dumps(compatible, ensure_ascii=False, default=str)
        if len(serialized) <= 1_500:
            return compatible
        return f"{serialized[:1500]}...<truncated>"

    def _diagnostic_headers(self, headers: object) -> dict[str, str]:
        header_names = (
            "retry-after",
            "x-should-retry",
            "x-request-id",
            "request-id",
            "content-type",
        )
        values: dict[str, str] = {}
        for name in header_names:
            value = self._header_value(headers, name)
            if value:
                values[name] = value
        return values

    def _header_value(self, headers: object, name: str) -> str:
        raw_value: object | None = None
        if isinstance(headers, dict):
            raw_value = headers.get(name)
            if raw_value is None:
                raw_value = headers.get(name.title())
        else:
            getter = getattr(headers, "get", None)
            if getter is None:
                return ""
            raw_value = getter(name)
            if raw_value is None:
                raw_value = getter(name.title())
        if not isinstance(raw_value, str):
            return ""
        return raw_value.strip()

    def _tool_event_state(
        self,
        *,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
    ) -> str:
        if attempt_tool_outcome_event_emitted:
            return "tool_outcomes_emitted"
        if attempt_tool_call_event_emitted:
            return "tool_call_events_only"
        return "none"

    def _retry_blockers(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        if retry_error is None:
            blockers.append("retry_error_unclassified")
        elif not retry_error.retryable:
            blockers.append("retry_error_marked_non_retryable")
        if not self._retry_config.enabled:
            blockers.append("retry_disabled")
        if retry_number >= self._retry_config.max_retries:
            blockers.append("max_retries_exhausted")
        if attempt_text_emitted and not self._should_retry_after_text_side_effect(
            retry_error=retry_error
        ):
            blockers.append("text_already_emitted")
        if attempt_tool_outcome_event_emitted:
            blockers.append("tool_outcomes_emitted")
        if attempt_messages_committed:
            blockers.append("messages_already_committed")
        return tuple(blockers)

    def _extract_text(self, response: object) -> str:
        parts = getattr(response, "parts", None)
        if isinstance(parts, list):
            texts: list[str] = []
            for part in cast(list[object], parts):
                if isinstance(part, TextPart) and part.content:
                    texts.append(part.content)
            if texts:
                return "".join(texts)
            return ""
        return str(response)

    def _apply_streamed_text_fallback(
        self,
        messages: list[ModelRequest | ModelResponse],
        *,
        streamed_text: str,
    ) -> list[ModelRequest | ModelResponse]:
        if not streamed_text or not messages:
            return messages
        updated_messages = list(messages)
        for index in range(len(updated_messages) - 1, -1, -1):
            message = updated_messages[index]
            if not isinstance(message, ModelResponse):
                continue
            if any(isinstance(part, ToolCallPart) for part in message.parts):
                continue
            if not any(isinstance(part, TextPart) for part in message.parts):
                continue
            existing_text = self._extract_text(message)
            if existing_text == streamed_text:
                return updated_messages
            next_parts: list[ModelResponsePart] = []
            text_inserted = False
            for part in message.parts:
                if isinstance(part, TextPart):
                    if not text_inserted:
                        next_parts.append(TextPart(content=streamed_text))
                        text_inserted = True
                    continue
                next_parts.append(part)
            if not text_inserted:
                return updated_messages
            updated_messages[index] = replace(message, parts=next_parts)
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.stream_text_fallback_applied",
                message=(
                    "Repairing final assistant message with streamed text fallback"
                ),
                payload={
                    "original_text_length": len(existing_text),
                    "streamed_text_length": len(streamed_text),
                },
            )
            return updated_messages
        return updated_messages

    def _looks_like_tool_args_parse_failure(self, message: str) -> bool:
        lowered = message.strip().lower()
        if not lowered:
            return False
        indicators = (
            "expecting ',' delimiter",
            "expecting ':' delimiter",
            "expecting property name enclosed in double quotes",
            "expecting value",
            "invalid json",
            "tool arguments",
            "function.arguments",
        )
        return any(indicator in lowered for indicator in indicators)

    def _collect_salvageable_stream_tool_calls(
        self,
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> list[ToolCallPart]:
        salvageable: list[ToolCallPart] = []
        for index in sorted(streamed_tool_calls):
            item = streamed_tool_calls[index]
            if isinstance(item, ToolCallPart):
                salvageable.append(
                    self._normalize_salvaged_tool_call_for_recovery(item)
                )
                continue
            candidate = item.as_part()
            if candidate is not None:
                salvageable.append(
                    self._normalize_salvaged_tool_call_for_recovery(candidate)
                )
        return salvageable

    def _normalize_salvaged_tool_call_for_recovery(
        self,
        tool_call: ToolCallPart,
    ) -> ToolCallPart:
        repaired = repair_tool_args(tool_call.args)
        if repaired.repair_applied or repaired.fallback_invalid_json:
            self._log_salvaged_tool_call_repair(
                tool_call=tool_call,
                repaired=repaired,
            )
        return ToolCallPart(
            tool_name=tool_call.tool_name,
            args=repaired.normalized_args,
            tool_call_id=str(tool_call.tool_call_id or ""),
            id=tool_call.id,
            provider_name=tool_call.provider_name,
            provider_details=tool_call.provider_details,
        )

    def _log_salvaged_tool_call_repair(
        self,
        *,
        tool_call: ToolCallPart,
        repaired: ToolArgsRepairResult,
    ) -> None:
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.tool_call_args.salvaged_from_stream",
            message="Recovered malformed streamed tool arguments for continued execution",
            payload={
                "tool_name": tool_call.tool_name,
                "tool_call_id": str(tool_call.tool_call_id or ""),
                "repair_applied": repaired.repair_applied,
                "repair_succeeded": repaired.repair_succeeded,
                "fallback_invalid_json": repaired.fallback_invalid_json,
            },
        )

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
        if not self._looks_like_tool_args_parse_failure(error_message):
            return None
        salvageable_calls = self._collect_salvageable_stream_tool_calls(
            streamed_tool_calls
        )
        if not salvageable_calls:
            return None

        response_parts: list[TextPart | ToolCallPart] = []
        partial_text = "".join(emitted_text_chunks).strip()
        if partial_text:
            response_parts.append(TextPart(content=partial_text))
        response_parts.extend(salvageable_calls)
        assistant_response = ModelResponse(parts=response_parts)
        tool_error_parts = [
            ToolReturnPart(
                tool_name=tool_call.tool_name,
                tool_call_id=tool_call.tool_call_id,
                content=build_tool_error_result(
                    error_code="tool_input_validation_failed",
                    message=(
                        "Tool arguments were not valid JSON. "
                        "The provider rejected the malformed tool call before execution. "
                        f"Details: {error_message}"
                    ),
                ),
            )
            for tool_call in salvageable_calls
        ]
        tool_error_request = ModelRequest(parts=tool_error_parts)
        self._message_repo.prune_conversation_history_to_safe_boundary(
            self._conversation_id(request)
        )
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[assistant_response, tool_error_request],
        )
        self._publish_tool_call_events_from_messages(
            request=request,
            messages=[assistant_response],
            published_tool_call_ids=published_tool_call_ids,
        )
        self._publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=[tool_error_request],
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.tool_args_parse_failure.recovered",
            message="Recovered from malformed tool arguments by emitting an error tool result",
            payload={
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "tool_call_ids": [
                    str(tool_call.tool_call_id or "") for tool_call in salvageable_calls
                ],
            },
        )
        next_retry_number = retry_number + 1
        if next_retry_number >= total_attempts:
            log_event(
                LOGGER,
                logging.ERROR,
                event="llm.tool_args_parse_failure.recovery_exhausted",
                message=(
                    "Malformed tool argument recovery budget exhausted; failing request"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "retry_number": retry_number,
                    "total_attempts": total_attempts,
                },
            )
            self._raise_assistant_run_error(
                request=request,
                error_code="model_tool_args_invalid_json",
                error_message=error_message,
            )
        return await self._generate_async(
            request,
            retry_number=next_retry_number,
            total_attempts=total_attempts,
            skip_initial_user_prompt_persist=True,
        )

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
        if isinstance(stream_event, PartStartEvent):
            return self._handle_part_start_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
                streamed_tool_calls=streamed_tool_calls,
            )
        if isinstance(stream_event, PartDeltaEvent):
            return self._handle_part_delta_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
                streamed_tool_calls=streamed_tool_calls,
            )
        if isinstance(stream_event, PartEndEvent):
            return self._handle_part_end_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
                streamed_tool_calls=streamed_tool_calls,
            )
        return False

    def _handle_part_start_event(
        self,
        *,
        request: LLMRequest,
        event: PartStartEvent,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> bool:
        part = event.part
        if isinstance(part, TextPart):
            text_lengths.setdefault(event.index, 0)
            return self._emit_text_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_text_chunks=emitted_text_chunks,
                emitted_lengths=text_lengths,
            )
        if isinstance(part, ThinkingPart):
            if event.index not in started_thinking_parts:
                self._publish_thinking_started_event(
                    request=request,
                    part_index=event.index,
                )
                started_thinking_parts.add(event.index)
            thinking_lengths.setdefault(event.index, 0)
            return self._emit_thinking_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_lengths=thinking_lengths,
            )
        if isinstance(part, ToolCallPart):
            streamed_tool_calls[event.index] = part
        return False

    def _handle_part_delta_event(
        self,
        *,
        request: LLMRequest,
        event: PartDeltaEvent,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> bool:
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            text = str(delta.content_delta or "")
            if not text:
                return False
            text_lengths[event.index] = text_lengths.get(event.index, 0) + len(text)
            emitted_text_chunks.append(text)
            log_model_stream_chunk(request.role_id, text)
            self._publish_text_delta_event(request=request, text=text)
            return True
        if isinstance(delta, ThinkingPartDelta):
            if event.index not in started_thinking_parts:
                self._publish_thinking_started_event(
                    request=request,
                    part_index=event.index,
                )
                started_thinking_parts.add(event.index)
            text = str(delta.content_delta or "")
            if not text:
                return False
            thinking_lengths[event.index] = thinking_lengths.get(event.index, 0) + len(
                text
            )
            self._publish_thinking_delta_event(
                request=request,
                part_index=event.index,
                text=text,
            )
            return False
        if isinstance(delta, ToolCallPartDelta):
            existing = streamed_tool_calls.get(event.index)
            if existing is None:
                as_part = delta.as_part()
                streamed_tool_calls[event.index] = (
                    as_part if as_part is not None else delta
                )
            else:
                updated = delta.apply(existing)
                if isinstance(updated, (ToolCallPart, ToolCallPartDelta)):
                    streamed_tool_calls[event.index] = updated
        return False

    def _handle_part_end_event(
        self,
        *,
        request: LLMRequest,
        event: PartEndEvent,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> bool:
        part = event.part
        if isinstance(part, TextPart):
            return self._emit_text_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_text_chunks=emitted_text_chunks,
                emitted_lengths=text_lengths,
            )
        if isinstance(part, ThinkingPart):
            if event.index not in started_thinking_parts:
                self._publish_thinking_started_event(
                    request=request,
                    part_index=event.index,
                )
                started_thinking_parts.add(event.index)
            _ = self._emit_thinking_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_lengths=thinking_lengths,
            )
            self._publish_thinking_finished_event(
                request=request,
                part_index=event.index,
            )
        if isinstance(part, ToolCallPart):
            streamed_tool_calls[event.index] = part
        return False

    def _emit_text_suffix_for_part(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        content: str,
        emitted_text_chunks: list[str],
        emitted_lengths: dict[int, int],
    ) -> bool:
        previous_length = emitted_lengths.get(part_index, 0)
        suffix = content[previous_length:]
        emitted_lengths[part_index] = len(content)
        if not suffix:
            return False
        emitted_text_chunks.append(suffix)
        log_model_stream_chunk(request.role_id, suffix)
        self._publish_text_delta_event(request=request, text=suffix)
        return True

    def _emit_thinking_suffix_for_part(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        content: str,
        emitted_lengths: dict[int, int],
    ) -> bool:
        previous_length = emitted_lengths.get(part_index, 0)
        suffix = content[previous_length:]
        emitted_lengths[part_index] = len(content)
        if not suffix:
            return False
        self._publish_thinking_delta_event(
            request=request,
            part_index=part_index,
            text=suffix,
        )
        return False

    def _publish_text_delta_event(
        self,
        *,
        request: LLMRequest,
        text: str,
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.TEXT_DELTA,
                payload_json=dumps(
                    {
                        "text": text,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    }
                ),
            )
        )

    def _publish_thinking_started_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.THINKING_STARTED,
                payload_json=dumps(
                    {
                        "part_index": part_index,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    }
                ),
            )
        )

    def _publish_thinking_delta_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        text: str,
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.THINKING_DELTA,
                payload_json=dumps(
                    {
                        "part_index": part_index,
                        "text": text,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    }
                ),
            )
        )

    def _publish_thinking_finished_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.THINKING_FINISHED,
                payload_json=dumps(
                    {
                        "part_index": part_index,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    }
                ),
            )
        )

    def _to_json(self, obj: object) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"error": "unserializable", "repr": str(obj)})

    def _filter_model_messages(
        self, messages: Sequence[ModelRequest | ModelResponse]
    ) -> list[ModelRequest | ModelResponse]:
        return list(messages)

    def _collect_pending_tool_calls(
        self, messages: Sequence[ModelRequest | ModelResponse]
    ) -> list[tuple[str, str]]:
        pending_tool_call_ids: dict[str, str] = {}
        for msg in messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id:
                        pending_tool_call_ids[tool_call_id] = str(part.tool_name)
                continue
            for part in msg.parts:
                tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                if not tool_call_id:
                    continue
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    pending_tool_call_ids.pop(tool_call_id, None)
        return list(pending_tool_call_ids.items())

    def _restore_pending_tool_results_from_state(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
    ) -> tuple[list[ModelRequest | ModelResponse], int]:
        recovered_parts: list[ToolReturnPart] = []
        recovered_tool_call_ids: list[str] = []
        recovered_tool_names: list[str] = []
        for tool_call_id, tool_name in self._collect_pending_tool_calls(
            pending_messages
        ):
            state = load_or_recover_tool_call_state(
                shared_store=self._shared_store,
                event_log=self._event_bus,
                trace_id=request.trace_id,
                task_id=request.task_id,
                tool_call_id=tool_call_id,
                task_repo=self._task_repo,
            )
            visible_envelope = self._visible_tool_result_from_state(
                state=state,
                expected_tool_name=tool_name,
            )
            if visible_envelope is None:
                continue
            recovered_parts.append(
                ToolReturnPart(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    content=visible_envelope,
                )
            )
            recovered_tool_call_ids.append(tool_call_id)
            recovered_tool_names.append(tool_name)
        if not recovered_parts:
            return list(pending_messages), 0
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.recovered_tool_results_for_resume",
            message=(
                "Recovered persisted tool results for pending tool calls before resume"
            ),
            payload=cast(
                dict[str, JsonValue],
                {
                    "run_id": request.run_id,
                    "task_id": request.task_id,
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "recovered_tool_call_ids": recovered_tool_call_ids,
                    "recovered_tool_names": recovered_tool_names,
                    "recovered_count": len(recovered_parts),
                },
            ),
        )
        next_pending_messages = list(pending_messages)
        next_pending_messages.append(ModelRequest(parts=recovered_parts))
        return next_pending_messages, len(recovered_parts)

    def _visible_tool_result_from_state(
        self,
        *,
        state: PersistedToolCallState | None,
        expected_tool_name: str,
    ) -> dict[str, JsonValue] | None:
        if state is None:
            return None
        tool_name = str(state.tool_name or "").strip()
        if tool_name != expected_tool_name:
            return None
        if state.execution_status not in (
            ToolExecutionStatus.COMPLETED,
            ToolExecutionStatus.FAILED,
        ):
            return None
        raw_result_envelope = state.result_envelope
        if not isinstance(raw_result_envelope, dict):
            return None
        visible_result = self._visible_tool_result_from_envelope(raw_result_envelope)
        if not isinstance(visible_result, dict):
            return None
        if not self._tool_result_event_was_published(
            result_envelope=raw_result_envelope,
            visible_result=visible_result,
        ):
            return None
        normalized = self._to_json_compatible(visible_result)
        if not isinstance(normalized, dict):
            return None
        sanitized = sanitize_task_status_payload(normalized)
        if not isinstance(sanitized, dict):
            return None
        return cast(dict[str, JsonValue], sanitized)

    @staticmethod
    def _visible_tool_result_from_envelope(
        result_envelope: dict[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        raw_visible_result = result_envelope.get("visible_result")
        if isinstance(raw_visible_result, dict):
            return cast(dict[str, JsonValue], raw_visible_result)
        return result_envelope

    @staticmethod
    def _tool_result_event_was_published(
        *,
        result_envelope: dict[str, JsonValue],
        visible_result: dict[str, JsonValue] | None = None,
    ) -> bool:
        runtime_meta = result_envelope.get("runtime_meta")
        if isinstance(runtime_meta, dict):
            return runtime_meta.get("tool_result_event_published") is True
        envelope = result_envelope if visible_result is None else visible_result
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            return False
        return meta.get("tool_result_event_published") is True

    def _has_tool_side_effect_messages(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        for msg in messages:
            if isinstance(msg, ModelResponse):
                if any(isinstance(part, ToolCallPart) for part in msg.parts):
                    return True
                continue
            if any(
                isinstance(part, (ToolReturnPart, RetryPromptPart))
                for part in msg.parts
            ):
                return True
        return False

    def _truncate_history_to_safe_boundary(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        messages = list(history)
        safe_index = self._last_committable_index(messages)
        return messages[:safe_index]

    def _load_safe_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        return self._truncate_history_to_safe_boundary(
            self._filter_model_messages(
                self._message_repo.get_history_for_conversation(conversation_id)
            )
        )

    async def _prepare_prompt_context(
        self,
        *,
        request: LLMRequest,
        conversation_id: str,
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> _PreparedPromptContext:
        history = self._load_safe_history_for_conversation(conversation_id)
        source_history = list(history)
        provisional_system_prompt = self._inject_compaction_summary(
            session_id=request.session_id,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )
        budget = await self._estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=provisional_system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        estimated_before_microcompact = (
            ConversationTokenEstimator().estimate_history_tokens(history)
        )
        estimated_after_microcompact = estimated_before_microcompact
        compacted_message_count = 0
        compacted_part_count = 0
        if self._conversation_microcompact_service is not None:
            microcompact_result = self._conversation_microcompact_service.apply(
                history=history,
                budget=budget,
            )
            history = list(microcompact_result.messages)
            estimated_before_microcompact = microcompact_result.estimated_tokens_before
            estimated_after_microcompact = microcompact_result.estimated_tokens_after
            compacted_message_count = microcompact_result.compacted_message_count
            compacted_part_count = microcompact_result.compacted_part_count
        history = await self._maybe_compact_history(
            request=request,
            history=history,
            source_history=source_history,
            conversation_id=conversation_id,
            budget=budget,
            estimated_tokens_before_microcompact=estimated_before_microcompact,
            estimated_tokens_after_microcompact=estimated_after_microcompact,
        )
        history = self._coerce_history_to_provider_safe_sequence(
            request=request,
            history=history,
        )
        final_system_prompt = self._inject_compaction_summary(
            session_id=request.session_id,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )
        final_budget = await self._estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=final_system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        return _PreparedPromptContext(
            history=tuple(history),
            system_prompt=final_system_prompt,
            budget=final_budget,
            estimated_history_tokens_before_microcompact=estimated_before_microcompact,
            estimated_history_tokens_after_microcompact=estimated_after_microcompact,
            microcompact_compacted_message_count=compacted_message_count,
            microcompact_compacted_part_count=compacted_part_count,
        )

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
        _PreparedPromptContext,
        list[ModelRequest | ModelResponse],
        str,
        _CoordinationAgent,
    ]:
        prepared_prompt = await self._prepare_prompt_context(
            request=request,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        history = list(prepared_prompt.history)
        self._validate_history_input_capabilities(history)
        prepared_system_prompt = prepared_prompt.system_prompt
        model_settings = await self._build_model_settings(
            request=request,
            history=history,
            system_prompt=prepared_system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        hook_service = getattr(self, "_hook_service", None)
        agent = cast(
            _CoordinationAgent,
            build_coordination_agent(
                model_name=self._config.model,
                base_url=self._config.base_url,
                api_key=self._config.api_key,
                headers=self._config.headers,
                provider_type=self._config.provider,
                maas_auth=self._config.maas_auth,
                system_prompt=prepared_system_prompt,
                allowed_tools=allowed_tools,
                model_settings=model_settings,
                model_profile=resolve_openai_chat_model_profile(
                    base_url=self._config.base_url,
                    model_name=self._config.model,
                ),
                ssl_verify=self._config.ssl_verify,
                connect_timeout_seconds=self._config.connect_timeout_seconds,
                merged_env=(
                    resolved_hook_env
                    if (
                        hook_service is not None
                        and (
                            resolved_hook_env := hook_service.get_run_env(
                                request.run_id
                            )
                        )
                    )
                    else None
                ),
                llm_http_client_cache_scope=request.run_id,
                allowed_mcp_servers=allowed_mcp_servers,
                allowed_skills=allowed_skills,
                tool_registry=self._tool_registry,
                role_registry=self._role_registry,
                mcp_registry=self._mcp_registry,
                skill_registry=self._skill_registry,
            ),
        )
        return prepared_prompt, history, prepared_system_prompt, agent

    def _coerce_history_to_provider_safe_sequence(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        candidate_history = list(history)
        if is_replayable_history(candidate_history):
            return candidate_history
        replayable_start = self._first_tool_replayable_history_index(candidate_history)
        if replayable_start > 0:
            candidate_history = candidate_history[replayable_start:]
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.history.replayable_prefix.dropped",
                message=(
                    "Dropped a non-replayable history prefix before sending the "
                    "provider request"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "dropped_message_count": replayable_start,
                },
            )
        if candidate_history and is_replayable_history(candidate_history):
            return candidate_history
        if (
            candidate_history
            and not self._request_has_prompt_content(request)
            and history_has_valid_tool_replay(candidate_history)
        ):
            bridge_message = self._build_history_replay_bridge_message(request=request)
            if bridge_message is not None:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.history.replay_bridge.inserted",
                    message=(
                        "Inserted a synthetic user bridge before replaying "
                        "assistant/tool-only history"
                    ),
                    payload={
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                        "message_count": len(candidate_history),
                    },
                )
                return [bridge_message, *candidate_history]
        if self._request_has_prompt_content(request):
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.history.invalid_suffix.dropped",
                message=(
                    "Dropped non-replayable history and relied on the current user "
                    "prompt to restart the provider request"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "message_count": len(candidate_history),
                },
            )
            return []
        bridge_message = self._build_history_replay_bridge_message(request=request)
        if bridge_message is not None:
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.history.replay_bridge.synthetic_only",
                message=(
                    "Fell back to a synthetic user bridge because no replayable "
                    "history suffix remained"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                },
            )
            return [bridge_message]
        return []

    def _first_tool_replayable_history_index(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        if not history:
            return 0
        for index in range(len(history)):
            if history_has_valid_tool_replay(history[index:]):
                return index
        return len(history)

    def _build_history_replay_bridge_message(
        self,
        *,
        request: LLMRequest,
    ) -> ModelRequest | None:
        prompt = self._build_history_replay_bridge_prompt(request=request)
        if not prompt:
            return None
        return ModelRequest(parts=[UserPromptPart(content=prompt)])

    def _build_history_replay_bridge_prompt(
        self,
        *,
        request: LLMRequest,
    ) -> str:
        intent_text = ""
        run_intent_repo = getattr(self, "_run_intent_repo", None)
        try:
            if run_intent_repo is not None:
                intent_text = run_intent_repo.get(
                    request.run_id,
                    fallback_session_id=request.session_id,
                ).intent.strip()
        except KeyError:
            intent_text = ""
        lines = [
            "Continue the existing task using the compacted summary and preserved execution history.",
            "Resume from the latest in-progress state without discarding prior decisions or artifacts.",
        ]
        if intent_text:
            lines.extend(
                [
                    "",
                    "Original task intent:",
                    intent_text,
                ]
            )
        return "\n".join(lines).strip()

    async def _estimate_compaction_budget(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> ConversationCompactionBudget:
        del history
        estimated_mcp_context_tokens: int | None = None
        if self._config.context_window is not None and self._config.context_window > 0:
            estimated_mcp_context_tokens = await self._estimated_mcp_context_tokens(
                allowed_mcp_servers=allowed_mcp_servers
            )
        estimator = ConversationTokenEstimator()
        estimated_system_prompt_tokens = max(
            1,
            (len(system_prompt.encode("utf-8")) // _ESTIMATED_TOKEN_BYTES)
            + _ESTIMATED_TOKEN_OVERHEAD,
        )
        user_prompt = request.prompt_text.strip()
        estimated_user_prompt_tokens = (
            estimator.estimate_message_tokens(
                ModelRequest(parts=[UserPromptPart(content=user_prompt)])
            )
            if reserve_user_prompt_tokens and user_prompt
            else 0
        )
        estimated_tool_context_tokens = self._estimated_tool_context_tokens(
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
            estimated_mcp_context_tokens=estimated_mcp_context_tokens,
        )
        return build_conversation_compaction_budget(
            context_window=self._config.context_window,
            estimated_system_prompt_tokens=estimated_system_prompt_tokens,
            estimated_user_prompt_tokens=estimated_user_prompt_tokens,
            estimated_tool_context_tokens=estimated_tool_context_tokens,
            estimated_output_reserve_tokens=_COMPACTION_OUTPUT_RESERVE_TOKENS,
        )

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
        model_settings: OpenAIChatModelSettings = {
            # Some OpenAI-compatible providers return cumulative usage in each stream chunk.
            # Enabling this flag makes pydantic-ai keep the last chunk usage instead of summing chunks.
            "openai_continuous_usage_stats": True,
            "temperature": self._config.sampling.temperature,
            "top_p": self._config.sampling.top_p,
        }
        max_tokens = await self._safe_max_output_tokens(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        if max_tokens is not None:
            model_settings["max_tokens"] = max_tokens
        if request.thinking.enabled and request.thinking.effort is not None:
            model_settings["openai_reasoning_effort"] = request.thinking.effort
        return model_settings

    async def _safe_max_output_tokens(
        self,
        *,
        request: LLMRequest,
        history: Sequence[ModelRequest | ModelResponse],
        system_prompt: str,
        reserve_user_prompt_tokens: bool,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
    ) -> int | None:
        configured_max_tokens = self._config.sampling.max_tokens
        if configured_max_tokens is None:
            return None
        context_window = self._config.context_window
        if context_window is None or context_window <= 0:
            return configured_max_tokens
        estimator = ConversationTokenEstimator()
        estimated_history_tokens = estimator.estimate_history_tokens(history)
        budget = await self._estimate_compaction_budget(
            request=request,
            history=history,
            system_prompt=system_prompt,
            reserve_user_prompt_tokens=reserve_user_prompt_tokens,
            allowed_tools=allowed_tools,
            allowed_mcp_servers=allowed_mcp_servers,
            allowed_skills=allowed_skills,
        )
        reserved_tokens = estimated_history_tokens + budget.estimated_non_history_tokens
        available_output_tokens = context_window - reserved_tokens
        if available_output_tokens <= 0:
            return _MIN_AVAILABLE_OUTPUT_TOKENS
        return max(
            _MIN_AVAILABLE_OUTPUT_TOKENS,
            min(configured_max_tokens, available_output_tokens),
        )

    def _estimated_tool_context_tokens(
        self,
        *,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        estimated_mcp_context_tokens: int | None = None,
    ) -> int:
        if not allowed_tools and not allowed_mcp_servers and not allowed_skills:
            return 0
        reserved_chars = 0
        for tool_name in allowed_tools:
            descriptor = describe_builtin_tool(tool_name)
            if descriptor is not None:
                reserved_chars += _BUILTIN_TOOL_CONTEXT_CHARS
                continue
            reserved_chars += _EXTERNAL_TOOL_CONTEXT_CHARS
        reserved_chars += len(allowed_skills) * _SKILL_CONTEXT_CHARS
        builtin_and_skill_tokens = (
            max(
                0,
                (reserved_chars // _ESTIMATED_TOKEN_BYTES) + _ESTIMATED_TOKEN_OVERHEAD,
            )
            if reserved_chars > 0
            else 0
        )
        mcp_tokens = (
            estimated_mcp_context_tokens
            if estimated_mcp_context_tokens is not None
            else self._estimated_mcp_context_tokens_fallback(
                allowed_mcp_servers=allowed_mcp_servers
            )
        )
        return builtin_and_skill_tokens + mcp_tokens

    async def _estimated_mcp_context_tokens(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        if not allowed_mcp_servers:
            return 0
        resolved_server_names = self._mcp_registry.resolve_server_names(
            allowed_mcp_servers,
            strict=False,
            consumer="agents.execution.llm_session",
        )
        total_tokens = 0
        for server_name in resolved_server_names:
            cached_tokens = self._mcp_tool_context_token_cache.get(server_name)
            if cached_tokens is not None:
                total_tokens += cached_tokens
                continue
            try:
                tool_schemas = await self._mcp_registry.list_tool_schemas(server_name)
            except Exception as exc:
                fallback_tokens = self._estimated_mcp_context_tokens_fallback(
                    allowed_mcp_servers=(server_name,),
                )
                total_tokens += fallback_tokens
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.mcp_context_budget.estimate_failed",
                    message=(
                        "Failed to inspect MCP tool schemas for token budgeting; "
                        "falling back to heuristic reserve"
                    ),
                    payload={
                        "server_name": server_name,
                        "fallback_tokens": fallback_tokens,
                    },
                    exc_info=exc,
                )
                continue
            estimated_tokens = self._estimate_mcp_tool_schema_tokens(
                server_name=server_name,
                tool_schemas=tool_schemas,
            )
            self._mcp_tool_context_token_cache[server_name] = estimated_tokens
            total_tokens += estimated_tokens
        return total_tokens

    def _estimate_mcp_tool_schema_tokens(
        self,
        *,
        server_name: str,
        tool_schemas: tuple[McpToolSchema, ...],
    ) -> int:
        if not tool_schemas:
            return 0
        serialized_payload = json.dumps(
            [
                {
                    "server": server_name,
                    "tool": schema.model_dump(mode="json"),
                }
                for schema in tool_schemas
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return max(
            1,
            (len(serialized_payload) // _ESTIMATED_TOKEN_BYTES)
            + _ESTIMATED_TOKEN_OVERHEAD,
        )

    def _estimated_mcp_context_tokens_fallback(
        self,
        *,
        allowed_mcp_servers: tuple[str, ...],
    ) -> int:
        if not allowed_mcp_servers:
            return 0
        reserved_chars = len(allowed_mcp_servers) * _MCP_SERVER_CONTEXT_FALLBACK_CHARS
        return max(
            0,
            (reserved_chars // _ESTIMATED_TOKEN_BYTES) + _ESTIMATED_TOKEN_OVERHEAD,
        )

    async def _maybe_compact_history(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        source_history: Sequence[ModelRequest | ModelResponse] | None = None,
        conversation_id: str,
        budget: ConversationCompactionBudget,
        estimated_tokens_before_microcompact: int | None = None,
        estimated_tokens_after_microcompact: int | None = None,
    ) -> list[ModelRequest | ModelResponse]:
        if self._conversation_compaction_service is None:
            return history
        plan = self._conversation_compaction_service.plan_compaction(
            history=history,
            budget=budget,
        )
        if not plan.should_compact:
            return history
        hook_service = getattr(self, "_hook_service", None)
        if hook_service is not None:
            _ = await hook_service.execute(
                event_input=PreCompactInput(
                    event_name=HookEventName.PRE_COMPACT,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    run_kind=request.run_kind.value,
                    conversation_id=conversation_id,
                    message_count_before=len(history),
                    estimated_tokens_before=estimated_tokens_before_microcompact or 0,
                    estimated_tokens_after_microcompact=(
                        estimated_tokens_after_microcompact or 0
                    ),
                    threshold_tokens=plan.threshold_tokens,
                    target_tokens=plan.target_tokens,
                ),
                run_event_hub=self._run_event_hub,
            )
        compacted_result = await self._conversation_compaction_service.maybe_compact_with_result(
            session_id=request.session_id,
            role_id=request.role_id,
            conversation_id=conversation_id,
            history=history,
            source_history=source_history,
            budget=budget,
            estimated_tokens_before_microcompact=estimated_tokens_before_microcompact,
            estimated_tokens_after_microcompact=estimated_tokens_after_microcompact,
            plan=plan,
        )
        compacted_history = list(compacted_result.messages)
        if hook_service is not None and compacted_result.applied:
            _ = await hook_service.execute(
                event_input=PostCompactInput(
                    event_name=HookEventName.POST_COMPACT,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    run_kind=request.run_kind.value,
                    conversation_id=conversation_id,
                    message_count_before=len(history),
                    message_count_after=len(compacted_history),
                    estimated_tokens_before=estimated_tokens_before_microcompact or 0,
                    estimated_tokens_after=ConversationTokenEstimator().estimate_history_tokens(
                        compacted_history
                    ),
                ),
                run_event_hub=self._run_event_hub,
            )
        return compacted_history

    def _inject_compaction_summary(
        self,
        *,
        session_id: str,
        conversation_id: str,
        system_prompt: str,
    ) -> str:
        if self._conversation_compaction_service is None:
            return system_prompt
        prompt_section = self._conversation_compaction_service.build_prompt_section(
            session_id=session_id,
            conversation_id=conversation_id,
        )
        if not prompt_section:
            return system_prompt
        return f"{system_prompt.rstrip()}\n\n{prompt_section}".strip()

    async def _apply_user_prompt_hooks(
        self,
        request: LLMRequest,
    ) -> tuple[LLMRequest, tuple[str, ...]]:
        hook_service = getattr(self, "_hook_service", None)
        if hook_service is None:
            return request, ()
        prompt_text = self._resolve_hook_prompt_text(request)
        if not prompt_text:
            return request, ()
        bundle = await hook_service.execute(
            event_input=UserPromptSubmitInput(
                event_name=HookEventName.USER_PROMPT_SUBMIT,
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                user_prompt=prompt_text,
                input_parts=tuple(
                    part.model_dump(mode="json") for part in request.input
                ),
                run_kind=request.run_kind.value,
            ),
            run_event_hub=self._run_event_hub,
        )
        if bundle.decision == HookDecisionType.DENY:
            message = bundle.reason or "The prompt was blocked by runtime hooks."
            raise AssistantRunError(
                AssistantRunErrorPayload(
                    trace_id=request.trace_id,
                    session_id=request.session_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    conversation_id=self._conversation_id(request),
                    assistant_message=message,
                    error_code="prompt_denied",
                    error_message=message,
                )
            )
        next_request = request
        if bundle.updated_input is not None and isinstance(bundle.updated_input, str):
            next_request = request.model_copy(
                update={
                    "user_prompt": bundle.updated_input,
                    "input": (),
                }
            )
        return next_request, bundle.additional_context

    def _resolve_hook_prompt_text(self, request: LLMRequest) -> str:
        prompt_text = request.prompt_text.strip()
        if prompt_text:
            return prompt_text
        history = self._message_repo.get_history_for_conversation(
            self._conversation_id(request)
        )
        for message in reversed(history):
            if not isinstance(message, ModelRequest):
                continue
            resolved = self._extract_user_prompt_text(message)
            if resolved:
                return resolved
        return ""

    def _persist_hook_system_context_if_needed(
        self,
        *,
        request: LLMRequest,
        contexts: tuple[str, ...],
    ) -> None:
        conversation_id = self._conversation_id(request)
        for context in contexts:
            text = str(context).strip()
            if not text:
                continue
            self._message_repo.append_system_prompt_if_missing(
                session_id=request.session_id,
                workspace_id=request.workspace_id,
                conversation_id=conversation_id,
                agent_role_id=request.role_id,
                instance_id=request.instance_id,
                task_id=request.task_id,
                trace_id=request.trace_id,
                content=text,
            )

    def _persist_user_prompt_if_needed(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: UserPromptContent | None,
    ) -> tuple[list[ModelRequest | ModelResponse], bool]:
        if content is None:
            return history, False
        prompt_text = user_prompt_content_to_text(content)
        if not prompt_text:
            return history, False
        prompt_key = user_prompt_content_key(content)
        if self._history_ends_with_user_prompt(history, prompt_key):
            return history, False
        self._message_repo.prune_conversation_history_to_safe_boundary(
            self._conversation_id(request)
        )
        replaced = self._message_repo.replace_pending_user_prompt(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            content=content,
        )
        prompt_message = ModelRequest(parts=[UserPromptPart(content=content)])
        if replaced:
            return (
                self._filter_model_messages(
                    [
                        *self._message_repo.get_history_for_conversation(
                            self._conversation_id(request)
                        )
                    ]
                ),
                True,
            )
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[prompt_message],
        )
        next_history = list(history)
        next_history.append(prompt_message)
        return next_history, False

    def _history_ends_with_user_prompt(
        self,
        history: Sequence[ModelRequest | ModelResponse],
        content_key: str,
    ) -> bool:
        target = str(content_key or "").strip()
        if not target or not history:
            return False
        last = history[-1]
        if not isinstance(last, ModelRequest):
            return False
        parts = [part for part in last.parts if isinstance(part, UserPromptPart)]
        if len(parts) != len(last.parts):
            return False
        prompt_contents = [part.content for part in parts]
        current_key = user_prompt_content_key(
            prompt_contents[0] if len(prompt_contents) == 1 else prompt_contents
        )
        return current_key == target

    def _drop_duplicate_leading_request(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        new_messages: list[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        if not history or not new_messages:
            return new_messages
        last_history = history[-1]
        first_new = new_messages[0]
        if not isinstance(last_history, ModelRequest):
            return new_messages
        if not isinstance(first_new, ModelRequest):
            return new_messages
        if not self._model_requests_match_user_prompt(last_history, first_new):
            return new_messages
        return new_messages[1:]

    def _model_requests_match_user_prompt(
        self,
        left: ModelRequest,
        right: ModelRequest,
    ) -> bool:
        left_prompt = self._extract_user_prompt_text(left)
        if left_prompt is None:
            return False
        right_prompt = self._extract_user_prompt_text(right)
        if right_prompt is None:
            return False
        return left_prompt == right_prompt

    def _extract_user_prompt_text(self, message: ModelRequest) -> str | None:
        prompt_parts = [
            part for part in message.parts if isinstance(part, UserPromptPart)
        ]
        if len(prompt_parts) != len(message.parts):
            return None
        combined = "\n".join(
            user_prompt_content_to_text(part.content) for part in prompt_parts
        ).strip()
        return combined or None

    def _current_request_prompt_content(
        self,
        request: LLMRequest,
    ) -> UserPromptContent | None:
        if request.input:
            media_asset_service = self._prompt_content_persistence_service()
            if media_asset_service is not None:
                return media_asset_service.to_persisted_user_prompt_content(
                    parts=request.input
                )
        prompt = str(request.user_prompt or "").strip()
        return prompt or None

    def _request_has_prompt_content(self, request: LLMRequest) -> bool:
        return bool(request.prompt_text.strip())

    def _validate_request_input_capabilities(self, request: LLMRequest) -> None:
        self._validate_input_modalities_capabilities(
            self._request_input_modalities(request.input)
        )

    def _validate_history_input_capabilities(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        modalities: list[MediaModality] = []
        for message in history:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, UserPromptPart):
                    continue
                modalities.extend(
                    self._user_prompt_content_modalities(
                        cast(UserPromptContent, part.content)
                    )
                )
        self._validate_input_modalities_capabilities(tuple(modalities))

    def _validate_input_modalities_capabilities(
        self,
        modalities: Sequence[MediaModality],
    ) -> None:
        if not modalities:
            return
        unsupported: list[str] = []
        unknown: list[str] = []
        for modality in modalities:
            support = self._input_modality_support(modality)
            if support is True:
                continue
            if support is False:
                if modality.value not in unsupported:
                    unsupported.append(modality.value)
                continue
            if modality.value not in unknown:
                unknown.append(modality.value)
        if unsupported:
            raise ValueError(
                "This model does not support "
                f"{_format_modality_list(unsupported)} input. "
                "Remove the attachment or switch to a compatible model."
            )
        if unknown:
            raise ValueError(
                "This model's support for "
                f"{_format_modality_list(unknown)} input is unknown. "
                "Remove the attachment or switch to a model with explicit multimodal support."
            )

    def _request_input_modalities(
        self,
        parts: tuple[
            TextContentPart | MediaRefContentPart | InlineMediaContentPart, ...
        ],
    ) -> tuple[MediaModality, ...]:
        modalities: list[MediaModality] = []
        for part in parts:
            if isinstance(part, TextContentPart):
                continue
            modalities.append(part.modality)
        return tuple(modalities)

    def _user_prompt_content_modalities(
        self,
        content: UserPromptContent,
    ) -> tuple[MediaModality, ...]:
        modalities: list[MediaModality] = []
        self._collect_prompt_content_modalities(
            normalize_user_prompt_content(content), modalities
        )
        return tuple(modalities)

    def _collect_prompt_content_modalities(
        self,
        content: JsonValue,
        modalities: list[MediaModality],
    ) -> None:
        if isinstance(content, list):
            for item in content:
                self._collect_prompt_content_modalities(item, modalities)
            return
        if not isinstance(content, dict):
            return
        raw_modality = str(content.get("modality") or "").strip().lower()
        if not raw_modality:
            return
        try:
            modality = MediaModality(raw_modality)
        except ValueError:
            return
        modalities.append(modality)

    def _input_modality_support(self, modality: MediaModality) -> bool | None:
        input_capabilities = self._config.capabilities.input
        if modality == MediaModality.IMAGE:
            return input_capabilities.image
        if modality == MediaModality.AUDIO:
            return input_capabilities.audio
        return input_capabilities.video

    def _hydrate_history_media_content(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        media_asset_service = self._prompt_content_hydration_service()
        if media_asset_service is None:
            return list(history)
        hydrated_messages: list[ModelRequest | ModelResponse] = []
        for message in history:
            if not isinstance(message, ModelRequest):
                hydrated_messages.append(message)
                continue
            next_parts: list[ModelRequestPart] = []
            changed = False
            for part in message.parts:
                if not isinstance(part, UserPromptPart):
                    next_parts.append(part)
                    continue
                hydrated_content = media_asset_service.hydrate_user_prompt_content(
                    content=cast(UserPromptContent, part.content)
                )
                if hydrated_content != part.content:
                    changed = True
                next_parts.append(
                    UserPromptPart(
                        content=hydrated_content,
                        timestamp=part.timestamp,
                        part_kind=part.part_kind,
                    )
                )
            if changed:
                hydrated_messages.append(
                    clone_model_request_with_parts(message, next_parts)
                )
                continue
            hydrated_messages.append(message)
        return hydrated_messages

    def _prompt_content_persistence_service(
        self,
    ) -> _PromptContentPersistenceService | None:
        media_asset_service = getattr(self, "_media_asset_service", None)
        if not isinstance(media_asset_service, _PromptContentPersistenceService):
            return None
        return media_asset_service

    def _prompt_content_hydration_service(
        self,
    ) -> _PromptContentHydrationService | None:
        media_asset_service = getattr(self, "_media_asset_service", None)
        if not isinstance(media_asset_service, _PromptContentHydrationService):
            return None
        return media_asset_service

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
        safe_index = self._last_committable_index(pending_messages)
        if safe_index <= 0:
            return history, pending_messages, False, False
        raw_ready = pending_messages[:safe_index]
        committed_tool_validation_failures = self._has_tool_input_validation_failures(
            raw_ready
        )
        ready = self._normalize_committable_messages(raw_ready)
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=ready,
        )
        self._publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=ready,
        )
        next_history = self._filter_model_messages(
            self._message_repo.get_history_for_conversation(
                self._conversation_id(request)
            )
        )
        return (
            next_history,
            pending_messages[safe_index:],
            self._has_tool_side_effect_messages(ready),
            committed_tool_validation_failures,
        )

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
        next_history = history
        remaining = list(pending_messages)
        tool_events_published = False
        tool_validation_failures_committed = False
        while remaining:
            safe_index = self._last_committable_index(remaining)
            if safe_index <= 0:
                break
            (
                next_history,
                remaining,
                committed_tool_events_published,
                committed_tool_validation_failures,
            ) = self._commit_ready_messages(
                request=request,
                history=next_history,
                pending_messages=remaining,
            )
            if committed_tool_events_published:
                tool_events_published = True
            if committed_tool_validation_failures:
                tool_validation_failures_committed = True
        return (
            next_history,
            remaining,
            tool_events_published,
            tool_validation_failures_committed,
        )

    def _has_pending_tool_calls(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        return self._last_committable_index(messages) < len(messages)

    @staticmethod
    def _has_tool_input_validation_failures(
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_name:
                    return True
        return False

    def _normalize_committable_messages(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        normalized: list[ModelRequest | ModelResponse] = []
        for message in messages:
            if isinstance(message, ModelResponse):
                normalized.append(message)
                continue
            next_parts: list[ModelRequestPart] = []
            changed = False
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_name:
                    next_parts.append(
                        ToolReturnPart(
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            content=build_tool_error_result(
                                error_code="tool_input_validation_failed",
                                message=str(part.content or "").strip()
                                or "Tool input validation failed.",
                            ),
                        )
                    )
                    changed = True
                    continue
                next_parts.append(part)
            if changed:
                normalized.append(clone_model_request_with_parts(message, next_parts))
                continue
            normalized.append(message)
        return normalized

    @staticmethod
    def _normalize_tool_call_args_for_replay(
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        for message in messages:
            if not isinstance(message, ModelResponse):
                continue
            for part in message.parts:
                if not isinstance(part, ToolCallPart) or not isinstance(part.args, str):
                    continue
                repaired = repair_tool_args(part.args)
                if not repaired.repair_applied and not repaired.fallback_invalid_json:
                    continue
                part.args = repaired.arguments_json

    def _last_committable_index(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        pending: set[str] = set()
        last_safe_index = 0
        for index, msg in enumerate(messages, start=1):
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id:
                        pending.add(tool_call_id)
            else:
                for part in msg.parts:
                    tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                    if not tool_call_id:
                        continue
                    if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                        pending.discard(tool_call_id)
            if not pending:
                last_safe_index = index
        return last_safe_index

    def _maybe_enrich_tool_result_payload(
        self,
        *,
        tool_name: str,
        result_payload: JsonValue,
    ) -> JsonValue:
        descriptor = describe_builtin_tool(tool_name)
        if descriptor is None:
            try:
                server_names = self._mcp_registry.list_names()
            except AttributeError:
                server_names = ()
            for server_name in server_names:
                if not tool_name.startswith(f"{server_name}_"):
                    continue
                descriptor = describe_mcp_tool(
                    effective_tool_name=tool_name,
                    server_name=server_name,
                    source_scope=self._mcp_registry.get_spec(server_name).source,
                )
                break
        if descriptor is None:
            return result_payload
        if isinstance(result_payload, dict):
            payload_map = cast(dict[str, JsonValue], result_payload)
            if isinstance(payload_map.get("computer"), dict):
                return result_payload
            if "ok" in payload_map:
                next_payload = dict(payload_map)
                next_payload["data"] = self._computer_payload_from_raw_result(
                    descriptor=descriptor,
                    raw_result=payload_map.get("data"),
                )
                return cast(JsonValue, next_payload)
        return self._computer_payload_from_raw_result(
            descriptor=descriptor,
            raw_result=result_payload,
        )

    def _computer_payload_from_raw_result(
        self,
        *,
        descriptor: ComputerActionDescriptor,
        raw_result: JsonValue | None,
    ) -> JsonValue:
        if isinstance(raw_result, dict):
            raw_map = cast(dict[str, JsonValue], raw_result)
            if isinstance(raw_map.get("computer"), dict):
                return raw_result
            content = _content_payload(raw_map.get("content"))
            observation = _object_payload(raw_map.get("observation"))
            data = {
                key: value
                for key, value in raw_map.items()
                if key not in {"text", "content", "computer", "observation"}
            }
            return cast(
                JsonValue,
                build_computer_tool_payload(
                    descriptor=descriptor,
                    text=_display_text(raw_result),
                    content=content,
                    observation=observation,
                    data=data or None,
                ),
            )
        return cast(
            JsonValue,
            build_computer_tool_payload(
                descriptor=descriptor,
                text=_display_text(raw_result),
            ),
        )

    def _publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        emitted = False
        for msg in messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id and published_tool_call_ids is not None:
                        if tool_call_id in published_tool_call_ids:
                            continue
                        published_tool_call_ids.add(tool_call_id)
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOOL_CALL,
                            payload_json=self._to_json(
                                {
                                    "tool_name": part.tool_name,
                                    "tool_call_id": tool_call_id,
                                    "args": part.args,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )
                    emitted = True
        return emitted

    def _publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        for msg in messages:
            if isinstance(msg, ModelResponse):
                continue
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if self._tool_result_already_emitted_from_runtime(
                        request=request,
                        tool_name=str(part.tool_name),
                        tool_call_id=tool_call_id,
                    ):
                        continue
                    result_payload = cast(
                        JsonValue,
                        sanitize_task_status_payload(
                            self._to_json_compatible(cast(object, part.content))
                        ),
                    )
                    result_payload = self._maybe_enrich_tool_result_payload(
                        tool_name=str(part.tool_name),
                        result_payload=result_payload,
                    )
                    is_error = False
                    if isinstance(result_payload, dict):
                        payload_map = cast(dict[str, object], result_payload)
                        is_error = payload_map.get("ok") is False
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOOL_RESULT,
                            payload_json=self._to_json(
                                {
                                    "tool_name": str(part.tool_name),
                                    "tool_call_id": tool_call_id,
                                    "result": result_payload,
                                    "error": is_error,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )
                elif isinstance(part, RetryPromptPart) and part.tool_name:
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOOL_INPUT_VALIDATION_FAILED,
                            payload_json=self._to_json(
                                {
                                    "tool_name": part.tool_name,
                                    "tool_call_id": part.tool_call_id,
                                    "reason": "Input validation failed before tool execution.",
                                    "details": part.content,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )

    def _tool_result_already_emitted_from_runtime(
        self,
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
    ) -> bool:
        if not tool_call_id:
            return False
        state = load_tool_call_state(
            shared_store=self._shared_store,
            task_id=request.task_id,
            tool_call_id=tool_call_id,
        )
        if state is None or state.tool_name != tool_name:
            return False
        if state.execution_status not in (
            ToolExecutionStatus.COMPLETED,
            ToolExecutionStatus.FAILED,
        ):
            return False
        result_envelope = state.result_envelope
        if not isinstance(result_envelope, dict):
            return False
        visible_result = self._visible_tool_result_from_envelope(result_envelope)
        return self._tool_result_event_was_published(
            result_envelope=result_envelope,
            visible_result=visible_result,
        )

    def _to_json_compatible(self, value: object) -> JsonValue:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            entries = cast(list[object], value)
            return [self._to_json_compatible(entry) for entry in entries]
        if isinstance(value, dict):
            entries = cast(dict[object, object], value)
            return {
                str(key): self._to_json_compatible(entry)
                for key, entry in entries.items()
            }
        return str(value)

    def _workspace_id(self, request: LLMRequest) -> str:
        return request.workspace_id

    def _conversation_id(self, request: LLMRequest) -> str:
        return request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )

    def _resolve_tool_approval_policy(self, run_id: str) -> ToolApprovalPolicy:
        try:
            yolo = self._run_intent_repo.get(run_id).yolo
        except KeyError:
            yolo = False
        return self._tool_approval_policy.with_yolo(yolo)
