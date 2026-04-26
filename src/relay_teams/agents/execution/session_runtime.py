# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from copy import deepcopy
from collections.abc import AsyncIterator, Sequence
from json import dumps
from typing import Protocol, cast

from pydantic_ai._agent_graph import ModelRequestNode
from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_ai.usage import UsageLimits

from relay_teams.agents.execution.prompt_history import PreparedPromptContext
from relay_teams.agents.execution.session_mixin_base import AgentLlmSessionMixinBase
from relay_teams.logger import (
    close_model_stream,
    get_logger,
    log_event,
    log_model_output,
    log_model_stream_chunk,
)
from relay_teams.metrics.adapters import (
    record_session_step_async,
    record_token_usage_async,
)
from relay_teams.providers.llm_retry import extract_retry_error_info
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.agents.execution.recovery_flow import FallbackAttemptState
from relay_teams.workspace import build_conversation_id

LOGGER = get_logger(__name__)
LLM_REQUEST_LIMIT = 500


class AgentRunResult(Protocol):
    @property
    def response(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


class AgentNodeStream(Protocol):
    def __aiter__(self) -> AsyncIterator[object]: ...

    def stream_text(self, *, delta: bool) -> AsyncIterator[str]: ...

    def usage(self) -> object: ...


class AgentNodeStreamContext(Protocol):
    async def __aenter__(self) -> AgentNodeStream: ...

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None: ...


class StreamableModelRequestNode(Protocol):
    def stream(self, ctx: object) -> AgentNodeStreamContext: ...


class AgentRun(Protocol):
    ctx: object
    result: AgentRunResult | None

    async def __aenter__(self) -> "AgentRun": ...

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None: ...

    def __aiter__(self) -> "AgentRun": ...

    async def __anext__(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


class CoordinationAgent(Protocol):
    def iter(
        self,
        prompt: str | None,
        *,
        deps: ToolDeps,
        message_history: Sequence[ModelRequest | ModelResponse],
        usage_limits: UsageLimits,
    ) -> AgentRun: ...


def resolve_allowed_tools(
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


def model_step_payload(
    *,
    role_id: str,
    instance_id: str,
    prepared_prompt: PreparedPromptContext | None = None,
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
    payload.update(
        {
            "microcompact_applied": (
                prepared_prompt.microcompact_compacted_message_count > 0
                or prepared_prompt.microcompact_compacted_part_count > 0
            ),
            "estimated_tokens_before_microcompact": (
                prepared_prompt.estimated_history_tokens_before_microcompact
            ),
            "estimated_tokens_after_microcompact": (
                prepared_prompt.estimated_history_tokens_after_microcompact
            ),
            "microcompact_compacted_message_count": (
                prepared_prompt.microcompact_compacted_message_count
            ),
            "microcompact_compacted_part_count": (
                prepared_prompt.microcompact_compacted_part_count
            ),
        }
    )
    return payload


class SessionRuntimeMixin(AgentLlmSessionMixinBase):
    async def run(self, request: LLMRequest) -> str:
        return await self._generate_async(request)

    async def _generate_async(
        self,
        request: LLMRequest,
        *,
        retry_number: int = 0,
        total_attempts: int | None = None,
        skip_initial_user_prompt_persist: bool = False,
        fallback_state: FallbackAttemptState | None = None,
    ) -> str:
        resolved_fallback_state = (
            FallbackAttemptState.initial(getattr(self, "_profile_name", None))
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
                await self._persist_hook_system_context_if_needed_async(
                    request=request,
                    contexts=hook_system_contexts,
                )
        self._validate_request_input_capabilities(request)
        if self._metric_recorder is not None:
            await record_session_step_async(
                self._metric_recorder,
                workspace_id=resolved_workspace_id,
                session_id=request.session_id,
                run_id=request.run_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
            )
        allowed_tools = resolve_allowed_tools(
            self._tool_registry,
            self._allowed_tools,
            session_id=request.session_id,
        )
        await publish_run_event_async(
            self._run_event_hub,
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.MODEL_STEP_STARTED,
                payload_json=dumps(
                    model_step_payload(
                        role_id=request.role_id,
                        instance_id=request.instance_id,
                    )
                ),
            ),
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
            coordination_agent = cast(CoordinationAgent, agent)
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
                skill_registry=getattr(self, "_skill_registry", None),
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
                reminder_service=getattr(self, "_reminder_service", None),
                model_capabilities=self._config.capabilities,
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
                (
                    persisted_history,
                    rebuild_context,
                ) = await self._persist_user_prompt_if_needed_async(
                    request=request,
                    history=history,
                    content=self._current_request_prompt_content(request),
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
                    coordination_agent = cast(CoordinationAgent, agent)
                else:
                    history = persisted_history
            seen_count = 0
            buffered_messages: list[ModelRequest | ModelResponse] = []
            restarted = False
            result: AgentRunResult | None = None
            request_level_input_tokens = 0
            request_level_cached_input_tokens = 0
            request_level_output_tokens = 0
            request_level_reasoning_output_tokens = 0
            request_level_requests = 0
            latest_request_input_tokens = 0
            max_request_input_tokens = 0
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
                    provider_history = self._provider_history_for_model_turn(
                        request=request,
                        history=history,
                    )
                    async with coordination_agent.iter(
                        None,
                        deps=deps,
                        message_history=provider_history,
                        usage_limits=UsageLimits(request_limit=LLM_REQUEST_LIMIT),
                    ) as agent_run:
                        async for node in agent_run:
                            control_ctx.raise_if_cancelled()
                            if isinstance(node, ModelRequestNode):
                                streamable_node = cast(StreamableModelRequestNode, node)
                                streamed_tool_calls = cast(
                                    dict[int, ToolCallPart | ToolCallPartDelta],
                                    {},
                                )
                                streamed_text_start = len(emitted_text_chunks)
                                usage_before = deepcopy(agent_run.usage())
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
                                            text_emitted = await self._handle_model_stream_event_async(
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
                                                    request.role_id,
                                                    text_delta,
                                                )
                                                printed_any = True
                                                attempt_text_emitted = True
                                                if active_retry_number > 0:
                                                    active_retry_number = 0
                                                emitted_text_chunks.append(text_delta)
                                                await self._publish_text_delta_event_async(
                                                    request=request,
                                                    text=text_delta,
                                                )
                                usage_after = stream.usage()
                                input_tokens_delta = self._usage_delta_int(
                                    after=usage_after,
                                    before=usage_before,
                                    field_name="input_tokens",
                                )
                                request_level_input_tokens += input_tokens_delta
                                if input_tokens_delta > 0:
                                    latest_request_input_tokens = input_tokens_delta
                                    max_request_input_tokens = max(
                                        max_request_input_tokens,
                                        input_tokens_delta,
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

                            all_new = agent_run.new_messages()
                            new_batch = list(all_new)[seen_count:]
                            new_to_process = self._drop_duplicate_leading_request(
                                history=provider_history,
                                new_messages=new_batch,
                            )
                            new_to_process = self._apply_streamed_text_fallback(
                                new_to_process,
                                streamed_text=streamed_node_text,
                            )
                            if new_to_process:
                                if active_retry_number > 0:
                                    active_retry_number = 0
                                tool_call_events_emitted = await self._publish_tool_call_events_from_messages_async(
                                    request=request,
                                    messages=new_to_process,
                                    published_tool_call_ids=published_tool_call_ids,
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
                                ) = await self._commit_ready_messages_async(
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
                                    coordination_agent = cast(CoordinationAgent, agent)
                                    seen_count = 0
                                    buffered_messages = []
                                    restarted = True
                                    break
                            seen_count += len(new_batch)

                            if self._has_pending_tool_calls(buffered_messages):
                                continue
                            injections = self._injection_manager.drain_at_boundary(
                                request.run_id, request.instance_id
                            )
                            if injections:
                                for msg in injections:
                                    await publish_run_event_async(
                                        self._run_event_hub,
                                        RunEvent(
                                            session_id=request.session_id,
                                            run_id=request.run_id,
                                            trace_id=request.trace_id,
                                            task_id=request.task_id,
                                            instance_id=request.instance_id,
                                            role_id=request.role_id,
                                            event_type=RunEventType.INJECTION_APPLIED,
                                            payload_json=msg.model_dump_json(),
                                        ),
                                    )
                                    await self._message_repo.append_user_prompt_if_missing_async(
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
                                coordination_agent = cast(CoordinationAgent, agent)
                                seen_count = 0
                                buffered_messages = []
                                restarted = True
                                break

                    if not restarted:
                        maybe_result = agent_run.result
                        if maybe_result is None:
                            raise RuntimeError(
                                "Model run finished without a result object"
                            )
                        result = maybe_result
                        all_new = maybe_result.new_messages()
                        to_save = self._drop_duplicate_leading_request(
                            history=provider_history,
                            new_messages=list(all_new)[seen_count:],
                        )
                        to_save = self._apply_streamed_text_fallback(
                            to_save,
                            streamed_text=latest_streamed_text,
                        )
                        if to_save:
                            tool_call_events_emitted = await self._publish_tool_call_events_from_messages_async(
                                request=request,
                                messages=to_save,
                                published_tool_call_ids=published_tool_call_ids,
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
                        ) = await self._commit_all_safe_messages_async(
                            request=request,
                            history=history,
                            pending_messages=buffered_messages,
                        )
                        if committed_tool_events_published:
                            attempt_tool_outcome_event_emitted = True
                        if len(history) > previous_history_size:
                            attempt_messages_committed = True
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
                            latest_request_input_tokens = input_tokens
                            max_request_input_tokens = input_tokens
                        elif latest_request_input_tokens <= 0:
                            latest_request_input_tokens = input_tokens
                            max_request_input_tokens = max(
                                max_request_input_tokens,
                                latest_request_input_tokens,
                            )
                        tool_calls = self._usage_field_int(usage, "tool_calls")
                        if self._token_usage_repo is not None:
                            await self._token_usage_repo.record_async(
                                session_id=request.session_id,
                                run_id=request.run_id,
                                instance_id=request.instance_id,
                                role_id=request.role_id,
                                input_tokens=input_tokens,
                                cached_input_tokens=cached_input_tokens,
                                latest_input_tokens=latest_request_input_tokens,
                                max_input_tokens=max_request_input_tokens,
                                output_tokens=output_tokens,
                                reasoning_output_tokens=reasoning_output_tokens,
                                requests=requests,
                                tool_calls=tool_calls,
                                context_window=self._config.context_window,
                                model_profile=self._profile_name or "",
                            )
                        await publish_run_event_async(
                            self._run_event_hub,
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
                                        "latest_input_tokens": latest_request_input_tokens,
                                        "max_input_tokens": max_request_input_tokens,
                                        "output_tokens": output_tokens,
                                        "reasoning_output_tokens": reasoning_output_tokens,
                                        "total_tokens": input_tokens + output_tokens,
                                        "requests": requests,
                                        "tool_calls": tool_calls,
                                        "context_window": self._config.context_window,
                                        "model_profile": self._profile_name or "",
                                        "role_id": request.role_id,
                                        "instance_id": request.instance_id,
                                    }
                                ),
                            ),
                        )
                        if self._metric_recorder is not None:
                            await record_token_usage_async(
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
                                "latest_input_tokens": latest_request_input_tokens,
                                "max_input_tokens": max_request_input_tokens,
                                "output_tokens": output_tokens,
                                "reasoning_output_tokens": reasoning_output_tokens,
                                "requests": requests,
                                "tool_calls": tool_calls,
                                "context_window": self._config.context_window,
                                "model_profile": self._profile_name or "",
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            },
                        )
                        break
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
                    attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
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
                    attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
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
                await publish_run_event_async(
                    self._run_event_hub,
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
                    ),
                )
            if text and not printed_any:
                log_model_output(request.role_id, text)
            await publish_run_event_async(
                self._run_event_hub,
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.MODEL_STEP_FINISHED,
                    payload_json=dumps(
                        model_step_payload(
                            role_id=request.role_id,
                            instance_id=request.instance_id,
                            prepared_prompt=prepared_prompt,
                        )
                    ),
                ),
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
