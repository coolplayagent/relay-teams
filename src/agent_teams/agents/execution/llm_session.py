# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import asyncio
import json
import logging
from copy import deepcopy
from collections.abc import Sequence
from json import dumps
from typing import TYPE_CHECKING, Protocol, cast

from pydantic_ai._agent_graph import ModelRequestNode
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from agent_teams.providers.llm_retry import (
    LlmRetryErrorInfo,
    LlmRetrySchedule,
    compute_retry_delay_ms,
    extract_retry_error_info,
)
from agent_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from agent_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from agent_teams.logger import (
    close_model_stream,
    get_logger,
    log_event,
    log_model_output,
    log_model_stream_chunk,
)
from agent_teams.metrics import MetricRecorder
from agent_teams.metrics.adapters import record_session_step, record_token_usage
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.execution.coordination_agent_builder import (
    build_coordination_agent,
)
from agent_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from agent_teams.computer import (
    ComputerActionDescriptor,
    build_computer_tool_payload,
    describe_builtin_tool,
    describe_mcp_tool,
)
from agent_teams.media import MediaAssetService
from agent_teams.tools.registry import ToolRegistry, ToolResolutionContext
from agent_teams.tools.runtime import (
    ToolApprovalManager,
    ToolApprovalPolicy,
    ToolDeps,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.agents.execution.subagent_reflection import SubagentReflectionService
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
)

if TYPE_CHECKING:
    from agent_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from agent_teams.computer import ComputerRuntime
    from agent_teams.roles.role_registry import RoleRegistry
    from agent_teams.gateway.im import ImToolService

LOGGER = get_logger(__name__)
LLM_REQUEST_LIMIT = 500


class _AgentRunResult(Protocol):
    @property
    def response(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


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


class AgentLlmSession:
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        run_intent_repo: RunIntentRepository,
        workspace_manager: WorkspaceManager,
        media_asset_service: MediaAssetService | None,
        role_memory_service: RoleMemoryService | None,
        subagent_reflection_service: SubagentReflectionService | None,
        conversation_compaction_service: ConversationCompactionService | None,
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
        tool_approval_policy: ToolApprovalPolicy,
        notification_service: NotificationService | None = None,
        token_usage_repo: TokenUsageRepository | None = None,
        metric_recorder: MetricRecorder | None = None,
        retry_config: LlmRetryConfig | None = None,
        im_tool_service: "ImToolService | None" = None,
        computer_runtime: "ComputerRuntime | None" = None,
    ) -> None:
        self._config = config
        self._task_repo = task_repo
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._run_runtime_repo = run_runtime_repo
        self._run_intent_repo = run_intent_repo
        self._workspace_manager = workspace_manager
        self._media_asset_service = media_asset_service
        self._role_memory_service = role_memory_service
        self._subagent_reflection_service = subagent_reflection_service
        self._conversation_compaction_service = conversation_compaction_service
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
        self._tool_approval_policy = tool_approval_policy
        self._notification_service = notification_service
        self._token_usage_repo = token_usage_repo
        self._metric_recorder = metric_recorder
        self._retry_config = retry_config or LlmRetryConfig()
        self._im_tool_service = im_tool_service
        self._computer_runtime = computer_runtime

    async def run(self, request: LLMRequest) -> str:
        return await self._generate_async(request)

    async def _generate_async(
        self,
        request: LLMRequest,
        *,
        retry_number: int = 0,
        total_attempts: int | None = None,
    ) -> str:
        resolved_workspace_id = request.workspace_id
        resolved_conversation_id = request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )
        total_attempts = total_attempts or (self._retry_config.max_retries + 1)
        agent_system_prompt = request.system_prompt
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
                    {"role_id": request.role_id, "instance_id": request.instance_id}
                ),
            )
        )
        if self._metric_recorder is not None:
            record_session_step(
                self._metric_recorder,
                workspace_id=resolved_workspace_id,
                session_id=request.session_id,
                run_id=request.run_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
            )
        model_settings: OpenAIChatModelSettings = {
            # Some OpenAI-compatible providers return cumulative usage in each stream chunk.
            # Enabling this flag makes pydantic-ai keep the last chunk usage instead of summing chunks.
            "openai_continuous_usage_stats": True,
            "temperature": self._config.sampling.temperature,
            "top_p": self._config.sampling.top_p,
            "max_tokens": self._config.sampling.max_tokens,
        }
        if request.thinking.enabled and request.thinking.effort is not None:
            model_settings["openai_reasoning_effort"] = request.thinking.effort
        agent = build_coordination_agent(
            model_name=self._config.model,
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            system_prompt=agent_system_prompt,
            allowed_tools=_resolve_allowed_tools(
                self._tool_registry,
                self._allowed_tools,
                session_id=request.session_id,
            ),
            model_settings=model_settings,
            model_profile=resolve_openai_chat_model_profile(
                base_url=self._config.base_url,
                model_name=self._config.model,
            ),
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            allowed_mcp_servers=self._allowed_mcp_servers,
            allowed_skills=self._allowed_skills,
            tool_registry=self._tool_registry,
            mcp_registry=self._mcp_registry,
            skill_registry=self._skill_registry,
        )
        deps = ToolDeps(
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            message_repo=self._message_repo,
            approval_ticket_repo=self._approval_ticket_repo,
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
            tool_approval_policy=self._resolve_tool_approval_policy(request.run_id),
            metric_recorder=self._metric_recorder,
            notification_service=self._notification_service,
            im_tool_service=self._im_tool_service,
        )
        control_ctx = self._run_control_manager.context(
            run_id=request.run_id,
            instance_id=request.instance_id,
        )

        printed_any = False
        emitted_text_chunks: list[str] = []
        attempt_text_emitted = False
        attempt_tool_event_emitted = False
        attempt_messages_committed = False
        published_tool_call_ids: set[str] = set()
        history: list[ModelRequest | ModelResponse] = (
            self._truncate_history_to_safe_boundary(
                self._filter_model_messages(
                    self._message_repo.get_history_for_conversation(
                        resolved_conversation_id
                    )
                )
            )
        )
        history = await self._maybe_compact_history(
            request=request,
            history=history,
            conversation_id=resolved_conversation_id,
        )
        agent_system_prompt = self._inject_compaction_summary(
            session_id=request.session_id,
            conversation_id=resolved_conversation_id,
            system_prompt=agent_system_prompt,
        )
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
        history = self._persist_user_prompt_if_needed(
            request=request,
            history=history,
            content=request.user_prompt,
        )
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
                            usage_before = deepcopy(agent_run.usage())
                            # Stream text chunks from this model response in real-time
                            async with node.stream(agent_run.ctx) as stream:
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
                                        )
                                        if text_emitted:
                                            printed_any = True
                                            attempt_text_emitted = True
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
                            request_level_cached_input_tokens += self._usage_delta_int(
                                after=usage_after,
                                before=usage_before,
                                field_name="cache_read_tokens",
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

                        # After each node (ModelRequestNode or others like CallToolsNode),
                        # scan for new messages to emit tool call/result events
                        all_new = agent_run.new_messages()
                        new_batch = list(all_new)[seen_count:]
                        new_to_process = self._drop_duplicate_leading_request(
                            history=history,
                            new_messages=new_batch,
                        )
                        if new_to_process:
                            tool_call_events_emitted = (
                                self._publish_tool_call_events_from_messages(
                                    request=request,
                                    messages=new_to_process,
                                    published_tool_call_ids=published_tool_call_ids,
                                )
                            )
                            if tool_call_events_emitted:
                                attempt_tool_event_emitted = True
                            buffered_messages.extend(new_to_process)
                            previous_history_size = len(history)
                            (
                                history,
                                buffered_messages,
                                committed_tool_events_published,
                            ) = self._commit_ready_messages(
                                request=request,
                                history=history,
                                pending_messages=buffered_messages,
                            )
                            if committed_tool_events_published:
                                attempt_tool_event_emitted = True
                            if len(history) > previous_history_size:
                                attempt_messages_committed = True
                        seen_count += len(new_batch)

                        # Drain pending user injections at this boundary (already handled in previous version, check if needed here)
                        injections = self._injection_manager.drain_at_boundary(
                            request.run_id, request.instance_id
                        )
                        if injections:
                            extra = [
                                ModelRequest(
                                    parts=[UserPromptPart(content=msg.content)]
                                )
                                for msg in injections
                            ]
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
                            self._message_repo.append(
                                session_id=request.session_id,
                                workspace_id=resolved_workspace_id,
                                conversation_id=resolved_conversation_id,
                                agent_role_id=request.role_id,
                                instance_id=request.instance_id,
                                task_id=request.task_id,
                                trace_id=request.trace_id,
                                messages=extra,
                            )
                            attempt_messages_committed = True
                            # Restart iter() with injected messages appended to committed history
                            history = self._filter_model_messages(
                                self._message_repo.get_history_for_conversation(
                                    resolved_conversation_id
                                )
                            )
                            seen_count = 0
                            buffered_messages = []
                            restarted = True
                            break  # break inner for-loop, restart while

                if not restarted:
                    # Normal completion
                    maybe_result = agent_run.result
                    if maybe_result is None:
                        raise RuntimeError("Model run finished without a result object")
                    result = maybe_result
                    # Flush any remaining messages (e.g. final tool results)
                    all_new = result.new_messages()
                    to_save = self._drop_duplicate_leading_request(
                        history=history,
                        new_messages=list(all_new)[seen_count:],
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
                            attempt_tool_event_emitted = True
                        buffered_messages.extend(to_save)
                    previous_history_size = len(history)
                    (
                        history,
                        buffered_messages,
                        committed_tool_events_published,
                    ) = self._commit_all_safe_messages(
                        request=request,
                        history=history,
                        pending_messages=buffered_messages,
                    )
                    if committed_tool_events_published:
                        attempt_tool_event_emitted = True
                    if len(history) > previous_history_size:
                        attempt_messages_committed = True
                    # Record and publish token usage
                    usage = result.usage()
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
                        output_tokens = self._usage_field_int(usage, "output_tokens")
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
                exc_info=exc,
            )
            retry_error = extract_retry_error_info(exc)
            should_retry = self._should_retry_request(
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted or printed_any,
                attempt_tool_event_emitted=attempt_tool_event_emitted,
                attempt_messages_committed=attempt_messages_committed,
            )
            if should_retry:
                resolved_retry_error = retry_error
                assert resolved_retry_error is not None
                next_retry_number = retry_number + 1
                delay_ms = compute_retry_delay_ms(
                    config=self._retry_config,
                    retry_number=next_retry_number,
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
                return await self._generate_async(
                    request,
                    retry_number=next_retry_number,
                    total_attempts=total_attempts,
                )
            if retry_error is not None and retry_error.retryable:
                if (
                    self._retry_config.enabled
                    and retry_number >= self._retry_config.max_retries
                ):
                    self._handle_retry_exhausted(
                        request=request,
                        retry_number=retry_number,
                        total_attempts=total_attempts,
                        error=retry_error,
                    )
                raise self._build_recoverable_pause_error(
                    request=request,
                    error=retry_error,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    error_message=self._build_model_api_error_message(exc),
                ) from exc
            raise ModelAPIError(
                model_name=exc.model_name,
                message=self._build_model_api_error_message(exc),
            ) from exc
        except Exception as exc:
            retry_error = extract_retry_error_info(exc)
            should_retry = self._should_retry_request(
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted or printed_any,
                attempt_tool_event_emitted=attempt_tool_event_emitted,
                attempt_messages_committed=attempt_messages_committed,
            )
            if should_retry:
                resolved_retry_error = retry_error
                assert resolved_retry_error is not None
                next_retry_number = retry_number + 1
                delay_ms = compute_retry_delay_ms(
                    config=self._retry_config,
                    retry_number=next_retry_number,
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
                return await self._generate_async(
                    request,
                    retry_number=next_retry_number,
                    total_attempts=total_attempts,
                )
            if retry_error is not None:
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
                    exc_info=exc,
                )
                if retry_error.retryable and (
                    self._retry_config.enabled
                    and retry_number >= self._retry_config.max_retries
                ):
                    self._handle_retry_exhausted(
                        request=request,
                        retry_number=retry_number,
                        total_attempts=total_attempts,
                        error=retry_error,
                    )
                if retry_error.retryable:
                    raise self._build_recoverable_pause_error(
                        request=request,
                        error=retry_error,
                        retry_number=retry_number,
                        total_attempts=total_attempts,
                    ) from exc
            raise

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
                    {"role_id": request.role_id, "instance_id": request.instance_id}
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

    def _should_retry_request(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> bool:
        return (
            retry_error is not None
            and retry_error.retryable
            and self._retry_config.enabled
            and retry_number < self._retry_config.max_retries
            and not attempt_text_emitted
            and not attempt_tool_event_emitted
            and not attempt_messages_committed
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

    def _handle_model_stream_event(
        self,
        *,
        request: LLMRequest,
        stream_event: object,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
    ) -> bool:
        if isinstance(stream_event, PartStartEvent):
            return self._handle_part_start_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
            )
        if isinstance(stream_event, PartDeltaEvent):
            return self._handle_part_delta_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
            )
        if isinstance(stream_event, PartEndEvent):
            return self._handle_part_end_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
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

    async def _maybe_compact_history(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        if self._conversation_compaction_service is None:
            return history
        return await self._conversation_compaction_service.maybe_compact(
            session_id=request.session_id,
            role_id=request.role_id,
            conversation_id=conversation_id,
            history=history,
        )

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

    def _persist_user_prompt_if_needed(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: str | None,
    ) -> list[ModelRequest | ModelResponse]:
        prompt = str(content or "").strip()
        if not prompt:
            return history
        if self._history_ends_with_user_prompt(history, prompt):
            return history
        self._message_repo.prune_conversation_history_to_safe_boundary(
            self._conversation_id(request)
        )
        prompt_message = ModelRequest(parts=[UserPromptPart(content=prompt)])
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
        return self._filter_model_messages(
            self._message_repo.get_history_for_conversation(
                self._conversation_id(request)
            )
        )

    def _history_ends_with_user_prompt(
        self,
        history: Sequence[ModelRequest | ModelResponse],
        content: str,
    ) -> bool:
        target = str(content or "").strip()
        if not target or not history:
            return False
        last = history[-1]
        if not isinstance(last, ModelRequest):
            return False
        parts = [part for part in last.parts if isinstance(part, UserPromptPart)]
        if len(parts) != len(last.parts):
            return False
        return (
            "\n".join(str(part.content or "").strip() for part in parts).strip()
            == target
        )

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
            str(part.content or "").strip() for part in prompt_parts
        ).strip()
        return combined or None

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
    ]:
        safe_index = self._last_committable_index(pending_messages)
        if safe_index <= 0:
            return history, pending_messages, False
        ready = pending_messages[:safe_index]
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
    ]:
        next_history = history
        remaining = list(pending_messages)
        tool_events_published = False
        while remaining:
            safe_index = self._last_committable_index(remaining)
            if safe_index <= 0:
                break
            (
                next_history,
                remaining,
                committed_tool_events_published,
            ) = self._commit_ready_messages(
                request=request,
                history=next_history,
                pending_messages=remaining,
            )
            if committed_tool_events_published:
                tool_events_published = True
        return next_history, remaining, tool_events_published

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
                                    "tool_call_id": (
                                        str(part.tool_call_id)
                                        if part.tool_call_id
                                        else ""
                                    ),
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
