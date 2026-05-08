# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from copy import deepcopy
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from json import dumps, loads
from typing import Protocol, TypeVar, cast

import httpx
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode
from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ModelResponsePart,
    PartEndEvent,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits
from uuid import uuid4

from relay_teams.agents.execution.message_commit import (
    tool_input_validation_failure_to_tool_return,
)
from relay_teams.agents.execution.prompt_history import PreparedPromptContext
from relay_teams.agents.execution.session_mixin_base import AgentLlmSessionMixinBase
from relay_teams.agents.execution.spec_checkpoint import (
    SpecCheckpointDecision,
    build_spec_checkpoint_decision,
)
from relay_teams.agents.tasks.models import TaskRecord, TaskSpecArtifact
from relay_teams.agents.tasks.task_repository import TaskRepository as _TaskRepo
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
from relay_teams.media import user_prompt_content_to_text
from relay_teams.providers.llm_retry import extract_retry_error_info
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_tools import runtime_tools_for_role
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import (
    AsyncRunEventPublisher,
    SyncRunEventPublisher,
    publish_run_event_async,
)
from relay_teams.sessions.runs.injection_classification import (
    INJECTION_CLASSIFIER,
    InjectionBoundaryContext,
    InjectionDisposition,
    public_injection_payload_json,
)
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_models import InjectionMessage, RunEvent
from relay_teams.tools.registry import ToolRegistry, ToolResolutionContext
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.execution import (
    flush_tool_result_batch_async,
    tool_result_batching_active,
)
from relay_teams.tools.runtime.tool_result_batching import tool_result_batch_scope
from relay_teams.agents.execution.context_editing import (
    build_diff_injection,
    build_injection_message,
)
from relay_teams.agents.execution.recovery_flow import FallbackAttemptState
from relay_teams.agents.execution.relay_tool_step_executor import (
    AgentNodeStream,
    AgentNodeStreamContext,
    AgentRun,
    AgentRunResult,
    AutoHarnessRuntimeService,
    CoordinationAgent,
    StreamableModelRequestNode,
    StreamableToolCallNode,
    _InjectionRestartApplied,
    _try_execute_relay_tool_step_async,
)
from relay_teams.agents.execution.spec_drift_evaluator import evaluate_spec_drift
from relay_teams.workspace import build_conversation_id

LOGGER = get_logger(__name__)
LLM_REQUEST_LIMIT = 500
_LLM_CLIENT_CLOSE_TASKS: set[asyncio.Task[None]] = set()
_ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS: set[asyncio.Task[None]] = set()
_ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_MIN_TIMEOUT_SECONDS = 1.0
_ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_MAX_TIMEOUT_SECONDS = 30.0
StreamItemT = TypeVar("StreamItemT")


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


def resolve_role_allowed_tools(
    *,
    tool_registry: object,
    role_registry: object,
    role_id: str,
    fallback_allowed_tools: tuple[str, ...],
    session_id: str,
) -> tuple[str, ...]:
    try:
        role = cast(RoleRegistry, role_registry).get(role_id)
        configured_tools = runtime_tools_for_role(
            role_registry=cast(RoleRegistry, role_registry),
            role=role,
            consumer="agents.execution.session_runtime.resolve_role_allowed_tools",
        )
    except KeyError:
        configured_tools = fallback_allowed_tools
    return resolve_allowed_tools(
        tool_registry,
        configured_tools,
        session_id=session_id,
    )


def consume_auto_harness_dirty_tools(
    service: object | None,
    *,
    run_id: str,
    instance_id: str,
) -> tuple[str, ...]:
    if service is None:
        return ()
    return cast(AutoHarnessRuntimeService, service).consume_tools_dirty(
        run_id=run_id,
        instance_id=instance_id,
    )


async def resolve_is_root_task_context(
    task_repo: _TaskRepo,
    *,
    task_id: str,
) -> bool:
    try:
        task_record = await task_repo.get_async(task_id)
    except (AttributeError, KeyError):
        return False
    return task_record.envelope.parent_task_id is None


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
    def _schedule_run_scoped_llm_http_client_close(
        self,
        *,
        request: LLMRequest,
    ) -> None:
        close_task = asyncio.create_task(
            self._close_run_scoped_llm_http_client(request=request)
        )
        _LLM_CLIENT_CLOSE_TASKS.add(close_task)

        def _finish(completed_task: asyncio.Task[None]) -> None:
            _LLM_CLIENT_CLOSE_TASKS.discard(completed_task)
            try:
                completed_task.result()
            except asyncio.CancelledError:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="llm.http_client.close.cancelled",
                    message="Run-scoped LLM HTTP client close was cancelled",
                    payload={
                        "run_id": request.run_id,
                        "task_id": request.task_id,
                        "instance_id": request.instance_id,
                        "role_id": request.role_id,
                    },
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.http_client.close.failed",
                    message="Run-scoped LLM HTTP client close failed",
                    payload={
                        "run_id": request.run_id,
                        "task_id": request.task_id,
                        "instance_id": request.instance_id,
                        "role_id": request.role_id,
                    },
                    exc_info=exc,
                )

        close_task.add_done_callback(_finish)

    async def _publish_tool_outcome_event_from_stream_async(
        self,
        *,
        request: LLMRequest,
        stream_event: object,
        published_tool_outcome_ids: set[str],
    ) -> bool:
        if not isinstance(stream_event, FunctionToolResultEvent):
            return False
        result = stream_event.result
        if not isinstance(result, (ToolReturnPart, RetryPromptPart)):
            return False
        if isinstance(result, ToolReturnPart) and tool_result_batching_active():
            return False
        if isinstance(result, RetryPromptPart):
            if not result.tool_name:
                return False
            result = tool_input_validation_failure_to_tool_return(result)
        return await self._publish_committed_tool_outcome_events_from_messages_async(
            request=request,
            messages=[ModelRequest(parts=[result])],
            published_tool_outcome_ids=published_tool_outcome_ids,
        )

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
            workspace = await self._workspace_manager.resolve_async(
                session_id=request.session_id,
                role_id=request.role_id,
                instance_id=request.instance_id,
                workspace_id=resolved_workspace_id,
                conversation_id=resolved_conversation_id,
            )
            is_root_task_context = await resolve_is_root_task_context(
                self._task_repo,
                task_id=request.task_id,
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
                workspace=workspace,
                role_memory=self._role_memory_service,
                media_asset_service=self._media_asset_service,
                computer_runtime=self._computer_runtime,
                background_task_service=self._background_task_service,
                monitor_service=self._monitor_service,
                todo_service=getattr(self, "_todo_service", None),
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                is_root_task_context=is_root_task_context,
                session_id=request.session_id,
                session_mode=request.session_mode,
                run_kind=request.run_kind.value,
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
                tool_approval_policy=await self._resolve_tool_approval_policy_async(
                    request.run_id
                ),
                shell_approval_repo=self._shell_approval_repo,
                metric_recorder=self._metric_recorder,
                notification_service=self._notification_service,
                im_tool_service=self._im_tool_service,
                xiaoluban_notify_service=getattr(
                    self,
                    "_xiaoluban_notify_service",
                    None,
                ),
                gateway_session_lookup=getattr(
                    self,
                    "_gateway_session_lookup",
                    None,
                ),
                hook_service=hook_service,
                reminder_service=getattr(self, "_reminder_service", None),
                auto_harness_service=getattr(self, "_auto_harness_service", None),
                audit_service=getattr(self, "_audit_service", None),
                model_capabilities=self._config.capabilities,
                hook_runtime_env=hook_runtime_env,
            )
            control_ctx = self._run_control_manager.context(
                run_id=request.run_id,
                instance_id=request.instance_id,
            )
            llm_event_timeout_seconds = _llm_stream_event_timeout_seconds(
                self._config.connect_timeout_seconds
            )

            printed_any = False
            emitted_text_chunks: list[str] = []
            active_retry_number = retry_number
            attempt_text_emitted = False
            attempt_tool_call_event_emitted = False
            attempt_tool_outcome_event_emitted = False
            attempt_messages_committed = False
            published_tool_call_ids: set[str] = set()
            published_tool_outcome_ids: set[str] = set()
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
            startup_injections = (
                self._injection_manager.drain_system_reminders_at_start(
                    request.run_id, request.instance_id
                )
                if isinstance(self._injection_manager, RunInjectionManager)
                else ()
            )
            if startup_injections:
                startup_injection_appended = False
                for msg in startup_injections:
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
                            payload_json=public_injection_payload_json(msg),
                        ),
                    )
                    appended = (
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
                    )
                    if appended:
                        startup_injection_appended = True
                if startup_injection_appended:
                    (
                        prepared_prompt,
                        history,
                        agent_system_prompt,
                        agent,
                    ) = await self._build_agent_iteration_context(
                        request=request,
                        conversation_id=resolved_conversation_id,
                        system_prompt=agent_system_prompt,
                        reserve_user_prompt_tokens=False,
                        allowed_tools=allowed_tools,
                        allowed_mcp_servers=self._allowed_mcp_servers,
                        allowed_skills=self._allowed_skills,
                    )
                    coordination_agent = cast(CoordinationAgent, agent)
            (
                history,
                _recovered_batch_tool_count,
            ) = await self._recover_uncommitted_tool_batches_async(
                request=request,
                history=history,
                deps=deps,
                recover_ready_calls=(
                    retry_number == 0 and not skip_initial_user_prompt_persist
                ),
            )
            seen_count = 0
            buffered_messages: list[ModelRequest | ModelResponse] = []
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
            observed_stream_messages: list[ModelRequest | ModelResponse] = []
            latest_streamed_text = ""

            async def apply_injections(
                injections: tuple[InjectionMessage, ...],
                *,
                interrupted_current_step: bool,
                restart_scope: str,
                supersedes_pending_tool_calls: bool = False,
                final_answer_ready: bool = False,
            ) -> bool:
                nonlocal history
                nonlocal prepared_prompt
                nonlocal agent_system_prompt
                nonlocal coordination_agent
                nonlocal seen_count
                nonlocal buffered_messages
                nonlocal attempt_messages_committed

                boundary_context = InjectionBoundaryContext(
                    final_answer_ready=final_answer_ready,
                )
                applied_injections = [
                    injection
                    for injection in injections
                    if INJECTION_CLASSIFIER.disposition(
                        injection,
                        context=boundary_context,
                    )
                    == InjectionDisposition.APPLY
                ]
                if not applied_injections:
                    return False
                applied_injections = _coalesce_user_followup_injections(
                    applied_injections
                )
                history_size_before_injection_commit = len(history)
                (
                    history,
                    buffered_messages,
                    _tool_events_published,
                    _tool_validation_failures,
                ) = await self._commit_all_safe_messages_async(
                    request=request,
                    history=history,
                    pending_messages=buffered_messages,
                    published_tool_outcome_ids=published_tool_outcome_ids,
                )
                if len(history) > history_size_before_injection_commit:
                    attempt_messages_committed = True
                for injection, applied_injection_ids in applied_injections:
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
                            payload_json=_injection_payload_json(
                                injection,
                                interrupted_current_step=interrupted_current_step,
                                restart_scope=restart_scope,
                                supersedes_pending_tool_calls=(
                                    supersedes_pending_tool_calls
                                ),
                                applied_injection_ids=applied_injection_ids,
                            ),
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
                        content=injection.content,
                    )
                attempt_messages_committed = True
                (
                    prepared_prompt,
                    history,
                    agent_system_prompt,
                    rebuilt_agent,
                ) = await self._build_agent_iteration_context(
                    request=request,
                    conversation_id=resolved_conversation_id,
                    system_prompt=request.system_prompt,
                    reserve_user_prompt_tokens=False,
                    allowed_tools=allowed_tools,
                    allowed_mcp_servers=self._allowed_mcp_servers,
                    allowed_skills=self._allowed_skills,
                )
                coordination_agent = cast(CoordinationAgent, rebuilt_agent)
                seen_count = 0
                buffered_messages = []
                return True

            async def apply_interrupt_injections() -> bool:
                if not isinstance(self._injection_manager, RunInjectionManager):
                    return False
                interrupt_injections = self._injection_manager.drain_interrupt(
                    request.run_id,
                    request.instance_id,
                )
                if not interrupt_injections:
                    return False
                return await apply_injections(
                    tuple(interrupt_injections),
                    interrupted_current_step=True,
                    restart_scope="interrupt",
                    supersedes_pending_tool_calls=True,
                )

            async def apply_queued_injections_at_boundary(
                *,
                restart_scope: str = "turn_boundary",
                final_answer_ready: bool = False,
            ) -> bool:
                queued_injections = self._injection_manager.drain_at_boundary(
                    request.run_id,
                    request.instance_id,
                )
                if not queued_injections:
                    return False
                return await apply_injections(
                    tuple(queued_injections),
                    interrupted_current_step=False,
                    restart_scope=restart_scope,
                    final_answer_ready=final_answer_ready,
                )

            async def apply_spec_checkpoint_if_due() -> bool:
                nonlocal history
                nonlocal prepared_prompt
                nonlocal agent_system_prompt
                nonlocal coordination_agent
                nonlocal seen_count
                nonlocal buffered_messages

                decision = await _build_spec_checkpoint_decision_async(
                    task_repo=self._task_repo,
                    role_registry=self._role_registry,
                    request=request,
                    history=history,
                )
                if not decision.should_inject:
                    return False
                append_system_prompt = (
                    self._message_repo.append_system_prompt_if_missing_async
                )
                checkpoint_appended = await append_system_prompt(
                    session_id=request.session_id,
                    workspace_id=resolved_workspace_id,
                    conversation_id=resolved_conversation_id,
                    agent_role_id=request.role_id,
                    instance_id=request.instance_id,
                    task_id=request.task_id,
                    trace_id=request.trace_id,
                    content=decision.content,
                )
                if not checkpoint_appended:
                    return False
                await publish_run_event_async(
                    self._run_event_hub,
                    RunEvent(
                        session_id=request.session_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        instance_id=request.instance_id,
                        role_id=request.role_id,
                        event_type=RunEventType.SPEC_CHECKPOINT_APPLIED,
                        payload_json=dumps(
                            _spec_checkpoint_event_payload(
                                decision=decision,
                                request=request,
                            )
                        ),
                    ),
                )
                log_event(
                    LOGGER,
                    logging.INFO,
                    event="llm.spec_checkpoint.applied",
                    message="Applied automatic spec checkpoint refresh",
                    payload={
                        "run_id": request.run_id,
                        "task_id": request.task_id,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                        "sequence": decision.sequence,
                        "reason": decision.reason,
                    },
                )

                task_repo = self._task_repo
                task_record_for_policy: TaskRecord | None = None
                try:
                    task_record_for_policy = await task_repo.get_async(request.task_id)
                except KeyError:
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        event="spec_checkpoint.task_record_not_found",
                        message="No task record found; policy evaluation will be skipped",
                        payload={
                            "task_id": request.task_id,
                            "run_id": request.run_id,
                        },
                    )
                policy = (
                    task_record_for_policy.envelope.lifecycle.spec_checkpoint
                    if task_record_for_policy is not None
                    else None
                )

                if policy is not None and policy.auto_evaluate_drift:
                    await _evaluate_checkpoint_drift(
                        task_repo=self._task_repo,
                        task_record=task_record_for_policy,
                        request=request,
                        decision=decision,
                        run_event_hub=self._run_event_hub,
                    )

                # Context editing: inject incremental diff via message_repo
                # when spec checkpoint content changes, avoiding full re-sends.
                if (
                    decision.content
                    and len(decision.content) > 0
                    and agent_system_prompt
                    and agent_system_prompt != decision.content
                ):
                    edit_job = build_diff_injection(
                        task_id=request.task_id,
                        session_id=request.session_id,
                        run_id=request.run_id,
                        old_spec=agent_system_prompt,
                        new_spec=decision.content,
                    )
                    if "No changes detected" not in edit_job.diff_description:
                        edit_message = build_injection_message(edit_job)
                        was_appended = await self._message_repo.append_system_prompt_if_missing_async(
                            session_id=request.session_id,
                            workspace_id=resolved_workspace_id,
                            conversation_id=resolved_conversation_id,
                            agent_role_id=request.role_id,
                            instance_id=request.instance_id,
                            task_id=request.task_id,
                            trace_id=request.trace_id,
                            content=edit_message,
                        )
                        if was_appended:
                            log_event(
                                LOGGER,
                                logging.DEBUG,
                                event="llm.context_edit.injected",
                                message="Injected incremental context edit for spec checkpoint update",
                                payload={
                                    "run_id": request.run_id,
                                    "task_id": request.task_id,
                                },
                            )
                (
                    prepared_prompt,
                    history,
                    agent_system_prompt,
                    rebuilt_agent,
                ) = await self._build_agent_iteration_context(
                    request=request,
                    conversation_id=resolved_conversation_id,
                    system_prompt=request.system_prompt,
                    reserve_user_prompt_tokens=False,
                    allowed_tools=allowed_tools,
                    allowed_mcp_servers=self._allowed_mcp_servers,
                    allowed_skills=self._allowed_skills,
                )
                coordination_agent = cast(CoordinationAgent, rebuilt_agent)
                seen_count = 0
                buffered_messages = []
                return True

            async def process_safe_boundary(
                boundary_agent_run: AgentRun,
                *,
                streamed_node_text: str,
                final_answer_ready: bool = False,
            ) -> bool:
                nonlocal history
                nonlocal prepared_prompt
                nonlocal agent_system_prompt
                nonlocal coordination_agent
                nonlocal seen_count
                nonlocal buffered_messages
                nonlocal boundary_checked_after_latest_batch
                nonlocal active_retry_number
                nonlocal attempt_tool_call_event_emitted
                nonlocal attempt_tool_outcome_event_emitted
                nonlocal attempt_messages_committed
                nonlocal observed_stream_messages

                boundary_new_messages = boundary_agent_run.new_messages()
                new_batch = list(boundary_new_messages)[seen_count:]
                new_to_process = self._drop_duplicate_leading_request(
                    history=provider_history,
                    new_messages=new_batch,
                )
                new_to_process = self._apply_streamed_text_fallback(
                    new_to_process,
                    streamed_text=streamed_node_text,
                )
                boundary_missing_observed = _missing_stream_observed_messages(
                    [*history, *buffered_messages, *new_to_process],
                    observed_stream_messages,
                )
                if boundary_missing_observed:
                    new_to_process.extend(boundary_missing_observed)
                    observed_stream_messages = []
                boundary_has_activity = bool(buffered_messages or new_to_process)
                if new_to_process:
                    if active_retry_number > 0:
                        active_retry_number = 0
                    boundary_tool_call_events_emitted = (
                        await self._publish_tool_call_events_from_messages_async(
                            request=request,
                            messages=new_to_process,
                            published_tool_call_ids=published_tool_call_ids,
                        )
                    )
                    if boundary_tool_call_events_emitted:
                        attempt_tool_call_event_emitted = True
                    self._normalize_tool_call_args_for_replay(new_to_process)
                    buffered_messages.extend(new_to_process)
                    history_size_before_ready_commit = len(history)
                    (
                        history,
                        buffered_messages,
                        boundary_committed_tool_events_published,
                        boundary_committed_tool_validation_failures,
                    ) = await self._commit_ready_messages_async(
                        request=request,
                        history=history,
                        pending_messages=buffered_messages,
                        published_tool_outcome_ids=published_tool_outcome_ids,
                    )
                    if boundary_committed_tool_events_published:
                        attempt_tool_outcome_event_emitted = True
                    if len(history) > history_size_before_ready_commit:
                        attempt_messages_committed = True
                    if boundary_committed_tool_validation_failures:
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
                            rebuilt_agent_after_validation,
                        ) = await self._build_agent_iteration_context(
                            request=request,
                            conversation_id=resolved_conversation_id,
                            system_prompt=request.system_prompt,
                            reserve_user_prompt_tokens=False,
                            allowed_tools=allowed_tools,
                            allowed_mcp_servers=self._allowed_mcp_servers,
                            allowed_skills=self._allowed_skills,
                        )
                        coordination_agent = cast(
                            CoordinationAgent,
                            rebuilt_agent_after_validation,
                        )
                        seen_count = 0
                        buffered_messages = []
                        return True
                seen_count += len(new_batch)

                if self._has_pending_tool_calls(buffered_messages):
                    return False
                if not boundary_has_activity and not final_answer_ready:
                    return False
                boundary_checked_after_latest_batch = True
                boundary_final_answer_ready = (
                    final_answer_ready or _messages_include_final_answer(new_to_process)
                )
                if await apply_queued_injections_at_boundary(
                    final_answer_ready=boundary_final_answer_ready,
                ):
                    return True
                return (
                    not boundary_final_answer_ready
                    and await apply_spec_checkpoint_if_due()
                )
        except BaseException:
            self._schedule_run_scoped_llm_http_client_close(request=request)
            raise

        try:
            try:
                while True:
                    control_ctx.raise_if_cancelled()
                    if await apply_interrupt_injections():
                        continue
                    if await apply_queued_injections_at_boundary():
                        continue
                    if await apply_spec_checkpoint_if_due():
                        continue
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
                        boundary_checked_after_latest_batch = False
                        observed_stream_messages = []
                        async for node in _aiter_with_timeout(
                            cast(AsyncIterable[object], agent_run),
                            timeout_seconds=llm_event_timeout_seconds,
                        ):
                            try:
                                control_ctx.raise_if_cancelled()
                                if await apply_interrupt_injections():
                                    raise _InjectionRestartApplied
                                if await process_safe_boundary(
                                    agent_run,
                                    streamed_node_text="",
                                ):
                                    raise _InjectionRestartApplied
                                if isinstance(node, ModelRequestNode):
                                    streamable_node = cast(
                                        StreamableModelRequestNode,
                                        cast(object, node),
                                    )
                                    streamed_tool_calls = {}
                                    active_tool_call_batch_id = (
                                        f"toolbatch_{uuid4().hex[:16]}"
                                    )
                                    streamed_tool_call_parts: list[
                                        tuple[int, ToolCallPart]
                                    ] = []
                                    streamed_text_start = len(emitted_text_chunks)
                                    usage_before = deepcopy(agent_run.usage())
                                    stream_context = streamable_node.stream(
                                        agent_run.ctx
                                    )
                                    async with _llm_stream_context_with_timeout(
                                        stream_context,
                                        timeout_seconds=llm_event_timeout_seconds,
                                    ) as stream:
                                        stream_iter = getattr(stream, "__aiter__", None)
                                        if callable(stream_iter):
                                            text_lengths: dict[int, int] = {}
                                            thinking_lengths: dict[int, int] = {}
                                            started_thinking_parts: set[int] = set()
                                            async for (
                                                stream_event
                                            ) in _aiter_with_timeout(
                                                cast(AsyncIterable[object], stream),
                                                timeout_seconds=llm_event_timeout_seconds,
                                            ):
                                                control_ctx.raise_if_cancelled()
                                                if await apply_interrupt_injections():
                                                    raise _InjectionRestartApplied
                                                text_emitted = await self._handle_model_stream_event_async(
                                                    request=request,
                                                    stream_event=stream_event,
                                                    emitted_text_chunks=emitted_text_chunks,
                                                    text_lengths=text_lengths,
                                                    thinking_lengths=thinking_lengths,
                                                    started_thinking_parts=started_thinking_parts,
                                                    streamed_tool_calls=streamed_tool_calls,
                                                )
                                                if isinstance(
                                                    stream_event, PartEndEvent
                                                ) and isinstance(
                                                    stream_event.part, ToolCallPart
                                                ):
                                                    observed_stream_messages.append(
                                                        ModelResponse(
                                                            parts=[stream_event.part]
                                                        )
                                                    )
                                                    streamed_tool_call_parts.append(
                                                        (
                                                            stream_event.index,
                                                            stream_event.part,
                                                        )
                                                    )
                                                if text_emitted:
                                                    printed_any = True
                                                    attempt_text_emitted = True
                                                    if active_retry_number > 0:
                                                        active_retry_number = 0
                                        else:
                                            async for text_delta in _aiter_with_timeout(
                                                stream.stream_text(delta=True),
                                                timeout_seconds=llm_event_timeout_seconds,
                                            ):
                                                control_ctx.raise_if_cancelled()
                                                if await apply_interrupt_injections():
                                                    raise _InjectionRestartApplied
                                                if text_delta:
                                                    log_model_stream_chunk(
                                                        request.role_id,
                                                        text_delta,
                                                    )
                                                    printed_any = True
                                                    attempt_text_emitted = True
                                                    if active_retry_number > 0:
                                                        active_retry_number = 0
                                                    emitted_text_chunks.append(
                                                        text_delta
                                                    )
                                                    await self._publish_text_delta_event_async(
                                                        request=request,
                                                        text=text_delta,
                                                    )
                                    _raise_if_stream_finished_without_reason(stream)
                                    if await apply_interrupt_injections():
                                        raise _InjectionRestartApplied
                                    if streamed_tool_call_parts:
                                        tool_call_event_emitted = await self._event_publishing_service().publish_observed_tool_call_events_batch_async(
                                            request=request,
                                            tool_calls=tuple(streamed_tool_call_parts),
                                            batch_id=active_tool_call_batch_id,
                                            published_tool_call_ids=published_tool_call_ids,
                                        )
                                        if tool_call_event_emitted:
                                            attempt_tool_call_event_emitted = True
                                            await self._event_publishing_service().seal_tool_call_batch_async(
                                                request=request,
                                                batch_id=active_tool_call_batch_id,
                                                tool_calls=tuple(
                                                    streamed_tool_call_parts
                                                ),
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
                                    request_level_output_tokens += (
                                        self._usage_delta_int(
                                            after=usage_after,
                                            before=usage_before,
                                            field_name="output_tokens",
                                        )
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
                                    current_streamed_node_text = "".join(
                                        emitted_text_chunks[streamed_text_start:]
                                    )
                                    latest_streamed_text = current_streamed_node_text
                                elif isinstance(node, CallToolsNode):
                                    tool_node = cast(
                                        StreamableToolCallNode, cast(object, node)
                                    )
                                    with tool_result_batch_scope() as result_buffer:
                                        try:
                                            relay_tool_step_result = await _try_execute_relay_tool_step_async(
                                                node=node,
                                                agent_run_ctx=agent_run.ctx,
                                            )
                                            if relay_tool_step_result is not None:
                                                observed_stream_messages.extend(
                                                    relay_tool_step_result.observed_messages
                                                )
                                            else:
                                                async with tool_node.stream(
                                                    agent_run.ctx
                                                ) as tool_stream:
                                                    async for (
                                                        tool_stream_event
                                                    ) in tool_stream:
                                                        control_ctx.raise_if_cancelled()
                                                        if await apply_interrupt_injections():
                                                            raise _InjectionRestartApplied
                                                        observed_result = _observed_tool_result_message(
                                                            tool_stream_event
                                                        )
                                                        if observed_result is not None:
                                                            observed_result_ids = (
                                                                _tool_result_ids(
                                                                    [observed_result]
                                                                )
                                                            )
                                                            if not observed_result_ids.intersection(
                                                                published_tool_outcome_ids
                                                            ):
                                                                observed_stream_messages.append(
                                                                    observed_result
                                                                )
                                                        tool_outcome_emitted = await self._publish_tool_outcome_event_from_stream_async(
                                                            request=request,
                                                            stream_event=tool_stream_event,
                                                            published_tool_outcome_ids=published_tool_outcome_ids,
                                                        )
                                                        if tool_outcome_emitted:
                                                            attempt_tool_outcome_event_emitted = True
                                        finally:
                                            batch_outcome_emitted = await flush_tool_result_batch_async(
                                                result_buffer,
                                                published_tool_outcome_ids=published_tool_outcome_ids,
                                            )
                                            if batch_outcome_emitted:
                                                attempt_tool_outcome_event_emitted = (
                                                    True
                                                )
                                    if await apply_interrupt_injections():
                                        raise _InjectionRestartApplied
                                    current_streamed_node_text = ""
                                else:
                                    current_streamed_node_text = ""
                            except _InjectionRestartApplied:
                                restarted = True
                                break

                            if await process_safe_boundary(
                                agent_run,
                                streamed_node_text=current_streamed_node_text,
                            ):
                                restarted = True
                                break

                    if not restarted and await apply_interrupt_injections():
                        restarted = True

                    if not restarted and not boundary_checked_after_latest_batch:
                        restarted = await apply_queued_injections_at_boundary(
                            final_answer_ready=True,
                        )

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
                        missing_observed = _missing_stream_observed_messages(
                            [*history, *buffered_messages, *to_save],
                            observed_stream_messages,
                        )
                        if missing_observed:
                            to_save.extend(missing_observed)
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
                        history_size_before_final_commit = len(history)
                        (
                            history,
                            buffered_messages,
                            committed_tool_events_published,
                            _committed_tool_validation_failures,
                        ) = await self._commit_all_safe_messages_async(
                            request=request,
                            history=history,
                            pending_messages=buffered_messages,
                            published_tool_outcome_ids=published_tool_outcome_ids,
                        )
                        if committed_tool_events_published:
                            attempt_tool_outcome_event_emitted = True
                        if len(history) > history_size_before_final_commit:
                            attempt_messages_committed = True
                        dirty_tool_names = consume_auto_harness_dirty_tools(
                            getattr(self, "_auto_harness_service", None),
                            run_id=request.run_id,
                            instance_id=request.instance_id,
                        )
                        if dirty_tool_names:
                            allowed_tools = resolve_role_allowed_tools(
                                tool_registry=self._tool_registry,
                                role_registry=self._role_registry,
                                role_id=request.role_id,
                                fallback_allowed_tools=self._allowed_tools,
                                session_id=request.session_id,
                            )
                            log_event(
                                LOGGER,
                                logging.INFO,
                                event="llm.autoharness_tools.rebuild",
                                message=(
                                    "Restarting agent iteration after AutoHarness "
                                    "enabled generated tools"
                                ),
                                payload={
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                    "tool_names": list(dirty_tool_names),
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
                            continue
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
            self._schedule_run_scoped_llm_http_client_close(request=request)


def _observed_tool_result_message(
    stream_event: object,
) -> ModelRequest | None:
    if not isinstance(stream_event, FunctionToolResultEvent):
        return None
    result = stream_event.result
    if isinstance(result, RetryPromptPart):
        if not result.tool_name:
            return None
        return ModelRequest(parts=[result])
    if isinstance(result, ToolReturnPart):
        return ModelRequest(parts=[result])
    return None


def _raise_if_stream_finished_without_reason(stream: object) -> None:
    raw_stream = getattr(stream, "_raw_stream_response", None)
    if raw_stream is None:
        return
    raw_stream_type = type(raw_stream).__name__
    if "OpenAI" not in raw_stream_type and "OpenRouter" not in raw_stream_type:
        return
    if getattr(raw_stream, "finish_reason", None) is not None:
        return
    raise httpx.RemoteProtocolError(
        "LLM stream ended before the provider sent a finish reason."
    )


async def _aiter_with_timeout(
    aiterable: AsyncIterable[StreamItemT],
    *,
    timeout_seconds: float,
) -> AsyncIterator[StreamItemT]:
    iterator = aiter(aiterable)
    while True:
        try:
            yield await asyncio.wait_for(
                anext(iterator),
                timeout=timeout_seconds,
            )
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise httpx.ReadTimeout(
                "Timed out waiting for the next LLM stream event."
            ) from exc


@asynccontextmanager
async def _llm_stream_context_with_timeout(
    context: AgentNodeStreamContext,
    *,
    timeout_seconds: float,
) -> AsyncIterator[AgentNodeStream]:
    enter_task = asyncio.create_task(context.__aenter__())
    cleanup_timeout_seconds = _abandoned_llm_stream_context_cleanup_timeout_seconds(
        timeout_seconds
    )
    try:
        done, _pending = await asyncio.wait(
            {enter_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        _schedule_abandoned_llm_stream_context_cleanup(
            context=context,
            enter_task=enter_task,
            reason="cancelled_while_opening",
            cleanup_timeout_seconds=cleanup_timeout_seconds,
        )
        raise

    if enter_task not in done:
        _schedule_abandoned_llm_stream_context_cleanup(
            context=context,
            enter_task=enter_task,
            reason="open_timeout",
            cleanup_timeout_seconds=cleanup_timeout_seconds,
        )
        raise httpx.ReadTimeout("Timed out waiting for the LLM stream to open.")

    try:
        stream = enter_task.result()
    except TimeoutError as exc:
        raise httpx.ReadTimeout(
            "Timed out waiting for the LLM stream to open."
        ) from exc
    try:
        yield stream
    except asyncio.CancelledError as exc:
        suppress = await context.__aexit__(type(exc), exc, exc.__traceback__)
        if not suppress:
            raise
    except GeneratorExit as exc:
        suppress = await context.__aexit__(type(exc), exc, exc.__traceback__)
        if not suppress:
            raise
    except Exception as exc:
        suppress = await context.__aexit__(type(exc), exc, exc.__traceback__)
        if not suppress:
            raise
    else:
        await context.__aexit__(None, None, None)


def _schedule_abandoned_llm_stream_context_cleanup(
    *,
    context: AgentNodeStreamContext,
    enter_task: asyncio.Task[AgentNodeStream],
    reason: str,
    cleanup_timeout_seconds: float,
) -> None:
    cleanup_task = asyncio.create_task(
        _cleanup_abandoned_llm_stream_context(
            context=context,
            enter_task=enter_task,
            reason=reason,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
        )
    )
    _ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS.add(cleanup_task)

    def _finish(completed_task: asyncio.Task[None]) -> None:
        _ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS.discard(completed_task)
        try:
            completed_task.result()
        except asyncio.CancelledError:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="llm.stream_context.cleanup.cancelled",
                message="Abandoned LLM stream context cleanup was cancelled",
                payload={"reason": reason},
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.stream_context.cleanup.failed",
                message="Abandoned LLM stream context cleanup failed",
                payload={"reason": reason},
                exc_info=exc,
            )

    cleanup_task.add_done_callback(_finish)


async def _cleanup_abandoned_llm_stream_context(
    *,
    context: AgentNodeStreamContext,
    enter_task: asyncio.Task[AgentNodeStream],
    reason: str,
    cleanup_timeout_seconds: float,
) -> None:
    if not await _wait_for_abandoned_llm_stream_open(
        context=context,
        enter_task=enter_task,
        reason=reason,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
    ):
        return

    try:
        enter_task.result()
    except asyncio.CancelledError:
        _log_abandoned_llm_stream_open_cancelled(reason=reason)
        return
    except Exception as exc:
        log_event(
            LOGGER,
            logging.DEBUG,
            event="llm.stream_context.open.failed",
            message="Abandoned LLM stream context open task failed",
            payload={"reason": reason},
            exc_info=exc,
        )
        return

    await _close_abandoned_llm_stream_context(
        context=context,
        reason=reason,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
    )


async def _wait_for_abandoned_llm_stream_open(
    *,
    context: AgentNodeStreamContext,
    enter_task: asyncio.Task[AgentNodeStream],
    reason: str,
    cleanup_timeout_seconds: float,
) -> bool:
    try:
        done, _pending = await asyncio.wait(
            {enter_task},
            timeout=cleanup_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        enter_task.cancel()
        raise

    if enter_task in done:
        return True

    enter_task.cancel()
    _observe_abandoned_llm_stream_open_after_cleanup_timeout(
        context=context,
        enter_task=enter_task,
        reason=reason,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
    )
    log_event(
        LOGGER,
        logging.WARNING,
        event="llm.stream_context.open.cleanup_timeout",
        message="Timed out waiting for abandoned LLM stream context to open",
        payload={
            "reason": reason,
            "timeout_seconds": cleanup_timeout_seconds,
        },
    )
    return False


def _observe_abandoned_llm_stream_open_after_cleanup_timeout(
    *,
    context: AgentNodeStreamContext,
    enter_task: asyncio.Task[AgentNodeStream],
    reason: str,
    cleanup_timeout_seconds: float,
) -> None:
    def _finish(completed_task: asyncio.Task[AgentNodeStream]) -> None:
        try:
            completed_task.result()
        except asyncio.CancelledError:
            _log_abandoned_llm_stream_open_cancelled(reason=reason)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="llm.stream_context.open.failed_after_cleanup_timeout",
                message=(
                    "Abandoned LLM stream context open task failed after cleanup "
                    "timeout"
                ),
                payload={"reason": reason},
                exc_info=exc,
            )
        else:
            _schedule_abandoned_llm_stream_context_cleanup(
                context=context,
                enter_task=completed_task,
                reason=f"{reason}_open_completed_after_cleanup_timeout",
                cleanup_timeout_seconds=cleanup_timeout_seconds,
            )

    enter_task.add_done_callback(_finish)


def _log_abandoned_llm_stream_open_cancelled(*, reason: str) -> None:
    log_event(
        LOGGER,
        logging.DEBUG,
        event="llm.stream_context.open.cancelled",
        message="Abandoned LLM stream context open task was cancelled",
        payload={"reason": reason},
    )


async def _close_abandoned_llm_stream_context(
    *,
    context: AgentNodeStreamContext,
    reason: str,
    cleanup_timeout_seconds: float,
) -> None:
    abandonment = asyncio.CancelledError(
        "LLM stream context was abandoned before it could be consumed."
    )
    exit_task = asyncio.create_task(
        context.__aexit__(
            type(abandonment),
            abandonment,
            abandonment.__traceback__,
        )
    )
    try:
        done, _pending = await asyncio.wait(
            {exit_task},
            timeout=cleanup_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        exit_task.cancel()
        raise

    if exit_task not in done:
        exit_task.cancel()
        _observe_abandoned_llm_stream_exit_after_cleanup_timeout(
            exit_task=exit_task,
            reason=reason,
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.stream_context.exit.cleanup_timeout",
            message="Timed out closing abandoned LLM stream context",
            payload={
                "reason": reason,
                "timeout_seconds": cleanup_timeout_seconds,
            },
        )
        return

    try:
        exit_task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.stream_context.exit_failed",
            message="Failed to close abandoned LLM stream context",
            payload={"reason": reason},
            exc_info=exc,
        )


def _observe_abandoned_llm_stream_exit_after_cleanup_timeout(
    *,
    exit_task: asyncio.Task[bool | None],
    reason: str,
) -> None:
    def _finish(completed_task: asyncio.Task[bool | None]) -> None:
        try:
            completed_task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.stream_context.exit_failed_after_cleanup_timeout",
                message=(
                    "Abandoned LLM stream context exit failed after cleanup timeout"
                ),
                payload={"reason": reason},
                exc_info=exc,
            )

    exit_task.add_done_callback(_finish)


def _abandoned_llm_stream_context_cleanup_timeout_seconds(
    open_timeout_seconds: float,
) -> float:
    return min(
        _ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_MAX_TIMEOUT_SECONDS,
        max(
            _ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_MIN_TIMEOUT_SECONDS,
            open_timeout_seconds,
        ),
    )


def _llm_stream_event_timeout_seconds(connect_timeout_seconds: float) -> float:
    return max(5.0, connect_timeout_seconds * 2)


def _missing_stream_observed_messages(
    existing_messages: Sequence[ModelRequest | ModelResponse],
    observed_messages: Sequence[ModelRequest | ModelResponse],
) -> list[ModelRequest | ModelResponse]:
    tool_call_ids = _tool_call_ids(existing_messages)
    tool_result_ids = _tool_result_ids(existing_messages)
    missing: list[ModelRequest | ModelResponse] = []
    for message in observed_messages:
        if isinstance(message, ModelResponse):
            missing_parts: list[ModelResponsePart] = []
            for part in message.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                tool_call_id = str(part.tool_call_id or "").strip()
                if not tool_call_id or tool_call_id in tool_call_ids:
                    continue
                missing_parts.append(part)
                tool_call_ids.add(tool_call_id)
            if missing_parts:
                missing.append(ModelResponse(parts=missing_parts))
            continue
        missing_request_parts: list[ModelRequestPart] = []
        for part in message.parts:
            if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if not tool_call_id or tool_call_id in tool_result_ids:
                continue
            missing_request_parts.append(part)
            tool_result_ids.add(tool_call_id)
        if missing_request_parts:
            missing.append(ModelRequest(parts=missing_request_parts))
    return missing


def _tool_call_ids(
    messages: Sequence[ModelRequest | ModelResponse],
) -> set[str]:
    ids: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if tool_call_id:
                ids.add(tool_call_id)
    return ids


def _tool_result_ids(
    messages: Sequence[ModelRequest | ModelResponse],
) -> set[str]:
    ids: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if tool_call_id:
                ids.add(tool_call_id)
    return ids


def _injection_payload_json(
    message: InjectionMessage,
    *,
    interrupted_current_step: bool,
    restart_scope: str,
    supersedes_pending_tool_calls: bool,
    applied_injection_ids: Sequence[str],
) -> str:
    payload = loads(public_injection_payload_json(message))
    if not isinstance(payload, dict):
        payload = {}
    payload["applied_injection_ids"] = list(applied_injection_ids)
    payload["interrupted_current_step"] = interrupted_current_step
    payload["restart_scope"] = restart_scope
    payload["supersedes_pending_tool_calls"] = supersedes_pending_tool_calls
    return dumps(payload)


def _coalesce_user_followup_injections(
    messages: list[InjectionMessage],
) -> list[tuple[InjectionMessage, tuple[str, ...]]]:
    user_messages = [
        message
        for message in messages
        if message.source == InjectionSource.USER and message.visibility == "public"
    ]
    if len(user_messages) <= 1:
        return [(message, _applied_injection_ids(message)) for message in messages]
    user_ids = {id(message) for message in user_messages}
    first_user = user_messages[0]
    merged_injection_ids = tuple(
        injection_id
        for message in user_messages
        for injection_id in _applied_injection_ids(message)
    )
    merged_content = "\n\n".join(
        text
        for message in user_messages
        if (text := user_prompt_content_to_text(message.content).strip())
    ).strip()
    merged_user = first_user.model_copy(update={"content": merged_content})
    result: list[tuple[InjectionMessage, tuple[str, ...]]] = []
    inserted_user = False
    for message in messages:
        if id(message) not in user_ids:
            result.append((message, _applied_injection_ids(message)))
            continue
        if not inserted_user:
            result.append((merged_user, merged_injection_ids))
            inserted_user = True
    return result


def _applied_injection_ids(message: InjectionMessage) -> tuple[str, ...]:
    ordered_ids = [*message.superseded_injection_ids, message.injection_id]
    return tuple(dict.fromkeys(ordered_ids))


def _messages_include_final_answer(
    messages: Sequence[ModelRequest | ModelResponse],
) -> bool:
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        if any(isinstance(part, ToolCallPart) for part in message.parts):
            continue
        if any(isinstance(part, TextPart) for part in message.parts):
            return True
    return False


class _SpecCheckpointTaskRepository(Protocol):
    @staticmethod
    async def get_async(task_id: str) -> TaskRecord:
        raise NotImplementedError  # pragma: no cover

    @staticmethod
    async def get_spec_artifact_async(artifact_id: str) -> TaskSpecArtifact:
        raise NotImplementedError  # pragma: no cover


class _SpecCheckpointRoleRegistry(Protocol):
    @staticmethod
    def is_coordinator_role(role_id: str) -> bool:
        raise NotImplementedError  # pragma: no cover


async def _build_spec_checkpoint_decision_async(
    *,
    task_repo: _SpecCheckpointTaskRepository | None,
    role_registry: _SpecCheckpointRoleRegistry | None,
    request: LLMRequest,
    history: Sequence[ModelRequest | ModelResponse],
) -> SpecCheckpointDecision:
    if _role_uses_coordinator_checkpoint_exemption(
        role_registry=role_registry,
        role_id=request.role_id,
    ):
        return SpecCheckpointDecision()
    if task_repo is None:
        return SpecCheckpointDecision()
    try:
        task_record = await task_repo.get_async(request.task_id)
    except KeyError:
        return SpecCheckpointDecision()

    current_artifact_version: int | None = None
    artifact_id = task_record.envelope.spec_artifact_id
    if artifact_id is not None:
        try:
            artifact = await task_repo.get_spec_artifact_async(artifact_id)
            current_artifact_version = artifact.version
        except (KeyError, AttributeError):
            current_artifact_version = None

    return build_spec_checkpoint_decision(
        task=task_record.envelope,
        role_id=request.role_id,
        history=history,
        current_artifact_version=current_artifact_version,
    )


def _role_uses_coordinator_checkpoint_exemption(
    *,
    role_registry: _SpecCheckpointRoleRegistry | None,
    role_id: str,
) -> bool:
    if role_registry is None:
        return False
    try:
        return bool(role_registry.is_coordinator_role(role_id))
    except KeyError:
        return False


def _spec_checkpoint_event_payload(
    *,
    decision: SpecCheckpointDecision,
    request: LLMRequest,
) -> dict[str, JsonValue]:
    history_tokens_since_last_checkpoint = decision.history_tokens_since_last_checkpoint
    return {
        "role_id": request.role_id,
        "instance_id": request.instance_id,
        "task_id": request.task_id,
        "sequence": decision.sequence,
        "reason": decision.reason,
        "tool_calls_since_last_checkpoint": decision.tool_calls_since_last_checkpoint,
        "messages_since_last_checkpoint": decision.messages_since_last_checkpoint,
        "history_tokens_since_last_checkpoint": history_tokens_since_last_checkpoint,
    }


async def _evaluate_checkpoint_drift(
    *,
    task_repo: object,
    task_record: TaskRecord | None,
    request: LLMRequest,
    decision: SpecCheckpointDecision,
    run_event_hub: AsyncRunEventPublisher | SyncRunEventPublisher,
) -> None:
    if task_record is None:
        return
    envelope = task_record.envelope
    spec = envelope.spec
    if spec is None:
        return
    artifact_id = envelope.spec_artifact_id
    if artifact_id is None:
        return
    policy = envelope.lifecycle.spec_checkpoint

    try:
        typed_repo = task_repo if isinstance(task_repo, _TaskRepo) else None
        if typed_repo is None:
            return

        evaluator_llm = getattr(request, "llm_evaluator", None)
        if evaluator_llm is None:
            return

        evaluation = await evaluate_spec_drift(
            spec=spec,
            task_id=request.task_id,
            artifact_id=artifact_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
            checkpoint_seq=decision.sequence,
            evaluator=evaluator_llm,
            drift_score_threshold=policy.drift_score_threshold,
        )
        await typed_repo.save_spec_checkpoint_evaluation_async(evaluation)

        await publish_run_event_async(
            run_event_hub,
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.SPEC_CHECKPOINT_EVALUATED,
                payload_json=dumps(
                    {
                        "evaluation_id": evaluation.evaluation_id,
                        "task_id": evaluation.task_id,
                        "checkpoint_seq": evaluation.checkpoint_seq,
                        "overall_score": evaluation.overall_score,
                        "drift_detected": evaluation.drift_detected,
                        "fallback": evaluation.fallback,
                    }
                ),
            ),
        )
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="spec_checkpoint.drift_evaluation_error",
            message="Drift evaluation failed during checkpoint injection",
            payload={"error": str(exc), "task_id": request.task_id},
        )
