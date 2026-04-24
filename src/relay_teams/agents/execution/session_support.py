# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ast
import logging
import json
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from relay_teams.agents.execution.event_publishing import EventPublishingService
from relay_teams.agents.execution.failure_reporting import FailureHandlingService
from relay_teams.agents.execution.message_commit import MessageCommitService
from relay_teams.agents.execution.prompt_history import PromptHistoryService
from relay_teams.agents.execution.recovery_flow import AttemptRecoveryService
from relay_teams.agents.execution.recovery_flow import (
    RESUME_SUPERSEDED_TOOL_CALL_ERROR_CODE,
    RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
)
from relay_teams.agents.execution.session_mixin_base import AgentLlmSessionMixinBase
from relay_teams.agents.execution.stream_events import StreamEventService
from relay_teams.agents.execution.tool_args_recovery import ToolArgsRecoveryService
from relay_teams.agents.execution.tool_result_state import ToolResultStateService
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.logger import get_logger, log_event, log_model_stream_chunk
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.persistence.scope_models import ScopeRef, ScopeType
from relay_teams.providers.llm_retry import LlmRetryErrorInfo
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import build_tool_error_result
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    ToolExecutionStatus,
    load_or_recover_tool_call_state,
)
from relay_teams.workspace import build_conversation_id

LOGGER = get_logger(__name__)
_RECOVERED_SUBAGENT_DESCRIPTION = "Recovered spawn_subagent call"
_RECOVERED_SUBAGENT_PROMPT = "Recovered spawn_subagent prompt unavailable."


class _NullPromptHistoryMessageRepo:
    def get_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        _ = conversation_id
        return []

    def prune_conversation_history_to_safe_boundary(
        self,
        conversation_id: str,
    ) -> None:
        _ = conversation_id

    def append(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: list[ModelRequest | ModelResponse],
    ) -> None:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
            messages,
        )

    def append_system_prompt_if_missing(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
    ) -> None:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
            content,
        )

    def replace_pending_user_prompt(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: object,
    ) -> bool:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
            content,
        )
        return False


class _NullRunIntentRepo:
    def get(self, run_id: str, *, fallback_session_id: str | None = None) -> object:
        _ = (run_id, fallback_session_id)
        return type("_Intent", (), {"intent": ""})()


def _tool_result_error_code(result: dict[str, JsonValue]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str):
            return code.strip()
    visible_result = result.get("visible_result")
    if isinstance(visible_result, dict):
        return _tool_result_error_code(cast(dict[str, JsonValue], visible_result))
    return ""


class SessionSupportMixin(AgentLlmSessionMixinBase):
    def _build_model_api_error_message(self, error: ModelAPIError) -> str:
        return self._failure_handling_service().build_model_api_error_message(error)

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
        return self._failure_handling_service().model_api_error_diagnostics_payload(
            error=error,
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=should_resume_after_tool_outcomes,
            closed_pending_tool_call_count=closed_pending_tool_call_count,
            to_json_compatible=self._to_json_compatible,
            should_retry_after_text_side_effect=self._should_retry_after_text_side_effect,
        )

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
        return self._failure_handling_service().exception_retry_diagnostics_payload(
            error=error,
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=should_resume_after_tool_outcomes,
            closed_pending_tool_call_count=closed_pending_tool_call_count,
            to_json_compatible=self._to_json_compatible,
            should_retry_after_text_side_effect=self._should_retry_after_text_side_effect,
        )

    def _exception_chain(self, error: BaseException) -> tuple[BaseException, ...]:
        return self._failure_handling_service().exception_chain(error)

    def _is_proxy_auth_failure(self, chain: Sequence[BaseException]) -> bool:
        return self._failure_handling_service().is_proxy_auth_failure(chain)

    def _is_connect_timeout(self, chain: Sequence[BaseException]) -> bool:
        return self._failure_handling_service().is_connect_timeout(chain)

    def _deepest_distinct_exception_message(
        self,
        *,
        chain: Sequence[BaseException],
        primary_message: str,
    ) -> str | None:
        return self._failure_handling_service().deepest_distinct_exception_message(
            chain=chain,
            primary_message=primary_message,
        )

    def _exception_diagnostic_item(self, error: BaseException) -> dict[str, object]:
        return self._failure_handling_service().exception_diagnostic_item(
            error,
            to_json_compatible=self._to_json_compatible,
        )

    def _diagnostic_value(self, value: object) -> object:
        return self._failure_handling_service().diagnostic_value(
            value,
            to_json_compatible=self._to_json_compatible,
        )

    def _diagnostic_headers(self, headers: object) -> dict[str, str]:
        return self._failure_handling_service().diagnostic_headers(headers)

    def _header_value(self, headers: object, name: str) -> str:
        return self._failure_handling_service().header_value(headers, name)

    def _tool_event_state(
        self,
        *,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
    ) -> str:
        return self._failure_handling_service().tool_event_state(
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
        )

    def _retry_blockers(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> tuple[str, ...]:
        return self._failure_handling_service().retry_blockers(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
            should_retry_after_text_side_effect=self._should_retry_after_text_side_effect,
        )

    def _extract_text(self, response: object) -> str:
        return self._failure_handling_service().extract_text(response)

    def _apply_streamed_text_fallback(
        self,
        messages: list[ModelRequest | ModelResponse],
        *,
        streamed_text: str,
    ) -> list[ModelRequest | ModelResponse]:
        return self._failure_handling_service().apply_streamed_text_fallback(
            messages,
            streamed_text=streamed_text,
            replace_message=lambda *, message, parts: replace(message, parts=parts),
        )

    def _looks_like_tool_args_parse_failure(self, message: str) -> bool:
        return self._stream_event_service().looks_like_tool_args_parse_failure(message)

    def _collect_salvageable_stream_tool_calls(
        self,
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> list[ToolCallPart]:
        return self._stream_event_service().collect_salvageable_stream_tool_calls(
            streamed_tool_calls
        )

    def _normalize_salvaged_tool_call_for_recovery(
        self,
        tool_call: ToolCallPart,
    ) -> ToolCallPart:
        return self._stream_event_service().normalize_salvaged_tool_call_for_recovery(
            tool_call
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
        return await self._tool_args_recovery_service().maybe_recover_from_tool_args_parse_failure(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            emitted_text_chunks=emitted_text_chunks,
            published_tool_call_ids=published_tool_call_ids,
            streamed_tool_calls=streamed_tool_calls,
            error_message=error_message,
            conversation_id=self._conversation_id,
            workspace_id=self._workspace_id,
            publish_tool_call_events_from_messages=(
                self._publish_tool_call_events_from_messages
            ),
            publish_committed_tool_outcome_events_from_messages=(
                self._publish_committed_tool_outcome_events_from_messages
            ),
            raise_assistant_run_error=self._raise_assistant_run_error,
            generate_async=self._generate_async,
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
        return self._stream_event_service().handle_model_stream_event(
            request=request,
            stream_event=stream_event,
            emitted_text_chunks=emitted_text_chunks,
            text_lengths=text_lengths,
            thinking_lengths=thinking_lengths,
            started_thinking_parts=started_thinking_parts,
            streamed_tool_calls=streamed_tool_calls,
            handle_part_start_event=self._handle_part_start_event,
            handle_part_delta_event=self._handle_part_delta_event,
            handle_part_end_event=self._handle_part_end_event,
        )

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
        return self._stream_event_service().handle_part_start_event(
            request=request,
            event=event,
            emitted_text_chunks=emitted_text_chunks,
            text_lengths=text_lengths,
            thinking_lengths=thinking_lengths,
            started_thinking_parts=started_thinking_parts,
            streamed_tool_calls=streamed_tool_calls,
            emit_text_suffix_for_part=self._emit_text_suffix_for_part,
            emit_thinking_suffix_for_part=self._emit_thinking_suffix_for_part,
            publish_thinking_started_event=self._publish_thinking_started_event,
        )

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
        return self._stream_event_service().handle_part_delta_event(
            request=request,
            event=event,
            emitted_text_chunks=emitted_text_chunks,
            text_lengths=text_lengths,
            thinking_lengths=thinking_lengths,
            started_thinking_parts=started_thinking_parts,
            streamed_tool_calls=streamed_tool_calls,
            log_model_stream_chunk=log_model_stream_chunk,
            publish_text_delta_event=self._publish_text_delta_event,
            publish_thinking_started_event=self._publish_thinking_started_event,
            publish_thinking_delta_event=self._publish_thinking_delta_event,
        )

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
        return self._stream_event_service().handle_part_end_event(
            request=request,
            event=event,
            emitted_text_chunks=emitted_text_chunks,
            text_lengths=text_lengths,
            thinking_lengths=thinking_lengths,
            started_thinking_parts=started_thinking_parts,
            streamed_tool_calls=streamed_tool_calls,
            emit_text_suffix_for_part=self._emit_text_suffix_for_part,
            emit_thinking_suffix_for_part=self._emit_thinking_suffix_for_part,
            publish_thinking_started_event=self._publish_thinking_started_event,
            publish_thinking_finished_event=self._publish_thinking_finished_event,
        )

    def _emit_text_suffix_for_part(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        content: str,
        emitted_text_chunks: list[str],
        emitted_lengths: dict[int, int],
    ) -> bool:
        return self._stream_event_service().emit_text_suffix_for_part(
            request=request,
            part_index=part_index,
            content=content,
            emitted_text_chunks=emitted_text_chunks,
            emitted_lengths=emitted_lengths,
            log_model_stream_chunk=log_model_stream_chunk,
            publish_text_delta_event=self._publish_text_delta_event,
        )

    def _emit_thinking_suffix_for_part(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        content: str,
        emitted_lengths: dict[int, int],
    ) -> bool:
        return self._stream_event_service().emit_thinking_suffix_for_part(
            request=request,
            part_index=part_index,
            content=content,
            emitted_lengths=emitted_lengths,
            publish_thinking_delta_event=self._publish_thinking_delta_event,
        )

    def _publish_text_delta_event(
        self,
        *,
        request: LLMRequest,
        text: str,
    ) -> None:
        self._event_publishing_service().publish_text_delta_event(
            request=request,
            text=text,
        )

    def _publish_thinking_started_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        self._event_publishing_service().publish_thinking_started_event(
            request=request,
            part_index=part_index,
        )

    def _publish_thinking_delta_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        text: str,
    ) -> None:
        self._event_publishing_service().publish_thinking_delta_event(
            request=request,
            part_index=part_index,
            text=text,
        )

    def _publish_thinking_finished_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        self._event_publishing_service().publish_thinking_finished_event(
            request=request,
            part_index=part_index,
        )

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

    async def _restore_pending_tool_results_from_state(
        self,
        *,
        request: LLMRequest,
        pending_messages: Sequence[ModelRequest | ModelResponse],
    ) -> tuple[list[ModelRequest | ModelResponse], int]:
        pending_tool_calls = {
            tool_call_id: tool_name
            for tool_call_id, tool_name in self._collect_pending_tool_calls(
                pending_messages
            )
        }
        committed_tool_call_ids = self._committed_tool_call_ids_for_request(request)
        recovered_tool_call_messages = self._recover_orphaned_spawn_subagent_calls(
            request=request,
            pending_tool_calls=pending_tool_calls,
            committed_tool_call_ids=committed_tool_call_ids,
        )
        recovered_orphaned_tool_call_ids = {
            str(part.tool_call_id or "").strip()
            for message in recovered_tool_call_messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        next_pending_messages = list(pending_messages)
        if recovered_tool_call_messages:
            next_pending_messages.extend(recovered_tool_call_messages)
            pending_tool_calls = {
                tool_call_id: tool_name
                for tool_call_id, tool_name in self._collect_pending_tool_calls(
                    next_pending_messages
                )
            }
        recovered_parts: list[ToolReturnPart] = []
        recovered_orphaned_parts: dict[str, ToolReturnPart] = {}
        recovered_tool_call_ids: list[str] = []
        recovered_tool_names: list[str] = []
        for tool_call_id, tool_name in pending_tool_calls.items():
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
            if (
                visible_envelope is None
                and state is not None
                and tool_name == "spawn_subagent"
            ):
                visible_envelope = await self._resume_pending_spawn_subagent_call(
                    request=request,
                    state=state,
                )
            if visible_envelope is None:
                continue
            recovered_part = ToolReturnPart(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=visible_envelope,
            )
            if tool_call_id in recovered_orphaned_tool_call_ids:
                recovered_orphaned_parts[tool_call_id] = recovered_part
            else:
                recovered_parts.append(recovered_part)
            recovered_tool_call_ids.append(tool_call_id)
            recovered_tool_names.append(tool_name)
        if recovered_orphaned_parts:
            next_pending_messages = self._interleave_recovered_orphaned_tool_results(
                next_pending_messages,
                recovered_orphaned_parts=recovered_orphaned_parts,
            )
        if not recovered_parts and not recovered_orphaned_parts:
            return next_pending_messages, 0
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
        if recovered_parts:
            next_pending_messages.append(ModelRequest(parts=recovered_parts))
        return next_pending_messages, len(recovered_parts) + len(
            recovered_orphaned_parts
        )

    def _interleave_recovered_orphaned_tool_results(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
        *,
        recovered_orphaned_parts: dict[str, ToolReturnPart],
    ) -> list[ModelRequest | ModelResponse]:
        interleaved: list[ModelRequest | ModelResponse] = []
        for message in messages:
            interleaved.append(message)
            if not isinstance(message, ModelResponse):
                continue
            message_recovered_parts = [
                recovered_orphaned_parts[tool_call_id]
                for part in message.parts
                if isinstance(part, ToolCallPart)
                for tool_call_id in (str(part.tool_call_id or "").strip(),)
                if tool_call_id in recovered_orphaned_parts
            ]
            if message_recovered_parts:
                interleaved.append(ModelRequest(parts=message_recovered_parts))
        return interleaved

    def _recover_orphaned_spawn_subagent_calls(
        self,
        *,
        request: LLMRequest,
        pending_tool_calls: dict[str, str],
        committed_tool_call_ids: set[str],
    ) -> list[ModelResponse]:
        recovered_parts: list[ToolCallPart] = []
        superseded_tool_call_ids = self._superseded_tool_call_ids_for_request(request)
        for state in self._task_tool_call_states(request.task_id):
            if str(state.run_id or "").strip() != request.run_id:
                continue
            if str(state.instance_id or "").strip() != request.instance_id:
                continue
            if str(state.role_id or "").strip() != request.role_id:
                continue
            tool_call_id = str(state.tool_call_id or "").strip()
            if not tool_call_id or tool_call_id in pending_tool_calls:
                continue
            if tool_call_id in committed_tool_call_ids:
                continue
            if tool_call_id in superseded_tool_call_ids:
                continue
            if self._state_has_superseded_tool_result(state):
                continue
            if str(state.tool_name or "").strip() != "spawn_subagent":
                continue
            recovered_part = self._spawn_subagent_tool_call_part_from_state(
                state=state,
                tool_call_id=tool_call_id,
            )
            if recovered_part is None:
                continue
            recovered_parts.append(recovered_part)
        return [ModelResponse(parts=[part]) for part in recovered_parts]

    def _spawn_subagent_tool_call_part_from_state(
        self,
        *,
        state: PersistedToolCallState,
        tool_call_id: str,
    ) -> ToolCallPart | None:
        args = self._spawn_subagent_args_from_state(state)
        if args is None:
            return None
        return ToolCallPart(
            tool_name="spawn_subagent",
            tool_call_id=tool_call_id,
            args=args,
        )

    def _spawn_subagent_args_from_state(
        self,
        state: PersistedToolCallState,
    ) -> dict[str, JsonValue] | None:
        call_state = state.call_state
        call_state_kind = str(call_state.get("kind") or "").strip()
        if call_state_kind and call_state_kind != "spawn_subagent_sync":
            return None
        source = (
            call_state
            if call_state_kind == "spawn_subagent_sync"
            else self._parse_json_object_text(state.args_preview)
        )
        if not source:
            return None
        if self._truthy_json_value(source.get("background")):
            return None
        requested_role_id = str(
            source.get("requested_role_id") or source.get("role_id") or ""
        ).strip()
        if not requested_role_id:
            return None
        description = str(source.get("description") or "").strip()
        if not description and source.get("description_len") is not None:
            description = _RECOVERED_SUBAGENT_DESCRIPTION
        prompt = str(source.get("prompt") or "").strip()
        if not prompt and source.get("prompt_len") is not None:
            prompt = _RECOVERED_SUBAGENT_PROMPT
        if not prompt:
            return None
        return {
            "role_id": requested_role_id,
            "description": description,
            "prompt": prompt,
            "background": False,
        }

    @staticmethod
    def _parse_json_object_text(value: str) -> dict[str, JsonValue]:
        text = value.strip()
        if not text:
            return {}
        decoded: object
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            try:
                decoded = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return {}
        if not isinstance(decoded, dict):
            return {}
        return {
            str(key): cast(JsonValue, item)
            for key, item in decoded.items()
            if isinstance(key, str)
        }

    @staticmethod
    def _truthy_json_value(value: JsonValue | None) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _superseded_tool_call_ids_for_request(self, request: LLMRequest) -> set[str]:
        event_bus = getattr(self, "_event_bus", None)
        if event_bus is None:
            return set()
        try:
            events = event_bus.list_by_trace(request.trace_id)
        except Exception:
            return set()
        superseded_ids: set[str] = set()
        for event in events:
            if str(event.get("event_type") or "") != RunEventType.TOOL_RESULT.value:
                continue
            payload_json = event.get("payload_json")
            if not isinstance(payload_json, str):
                continue
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            payload_map = cast(dict[str, JsonValue], payload)
            if not self._tool_result_event_matches_request_scope(
                event=cast(dict[str, JsonValue], event),
                payload=payload_map,
                request=request,
            ):
                continue
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            if not self._is_superseded_tool_result(cast(dict[str, JsonValue], result)):
                continue
            tool_call_id = str(payload.get("tool_call_id") or "").strip()
            if tool_call_id:
                superseded_ids.add(tool_call_id)
        return superseded_ids

    @staticmethod
    def _tool_result_event_matches_request_scope(
        *,
        event: dict[str, JsonValue],
        payload: dict[str, JsonValue],
        request: LLMRequest,
    ) -> bool:
        for source in (event, payload):
            instance_id = str(source.get("instance_id") or "").strip()
            if instance_id and instance_id != request.instance_id:
                return False
            role_id = str(source.get("role_id") or "").strip()
            if role_id and role_id != request.role_id:
                return False
        return True

    def _state_has_superseded_tool_result(self, state: PersistedToolCallState) -> bool:
        result_envelope = state.result_envelope
        if not isinstance(result_envelope, dict):
            return False
        return self._is_superseded_tool_result(result_envelope)

    @staticmethod
    def _is_superseded_tool_result(result: dict[str, JsonValue]) -> bool:
        return _tool_result_error_code(result) in {
            RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
            RESUME_SUPERSEDED_TOOL_CALL_ERROR_CODE,
        }

    def _committed_tool_call_ids_for_request(self, request: LLMRequest) -> set[str]:
        message_repo = getattr(self, "_message_repo", None)
        if message_repo is None:
            return set()
        resolved_conversation_id = request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )
        try:
            history = message_repo.get_history_for_conversation(
                resolved_conversation_id
            )
        except Exception:
            return set()
        committed_ids: set[str] = set()
        for message in history:
            for part in message.parts:
                if isinstance(part, ToolCallPart | ToolReturnPart):
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id:
                        committed_ids.add(tool_call_id)
        return committed_ids

    def _task_tool_call_states(
        self,
        task_id: str,
    ) -> tuple[PersistedToolCallState, ...]:
        shared_store = getattr(self, "_shared_store", None)
        if shared_store is None:
            return ()
        entries = shared_store.snapshot(
            ScopeRef(scope_type=ScopeType.TASK, scope_id=task_id)
        )
        states: list[PersistedToolCallState] = []
        for key, raw_value in entries:
            if not key.startswith("tool_call_state:"):
                continue
            try:
                state = PersistedToolCallState.model_validate_json(raw_value)
            except Exception:
                continue
            states.append(state)
        states.sort(key=lambda item: item.updated_at)
        return tuple(states)

    async def _resume_pending_spawn_subagent_call(
        self,
        *,
        request: LLMRequest,
        state: PersistedToolCallState,
    ) -> dict[str, JsonValue] | None:
        if state.execution_status not in {
            ToolExecutionStatus.READY,
            ToolExecutionStatus.RUNNING,
        }:
            return None
        call_state = state.call_state
        call_state_kind = str(call_state.get("kind") or "").strip()
        if call_state_kind and call_state_kind != "spawn_subagent_sync":
            return None
        background_task_service = getattr(self, "_background_task_service", None)
        if background_task_service is None:
            return None
        subagent_run_id = str(call_state.get("subagent_run_id") or "").strip()
        if not subagent_run_id:
            return None
        try:
            result = await background_task_service.wait_for_subagent_run(
                parent_run_id=request.run_id, subagent_run_id=subagent_run_id
            )
        except KeyError:
            return None
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            return cast(
                dict[str, JsonValue],
                build_tool_error_result(
                    error_code="subagent_execution_cancelled",
                    message="Subagent was cancelled during recovery",
                ),
            )
        except RuntimeError as exc:
            return cast(
                dict[str, JsonValue],
                build_tool_error_result(
                    error_code="subagent_execution_failed",
                    message=str(exc) or "Subagent failed",
                ),
            )
        return {
            "ok": True,
            "data": {
                "completed": True,
                "output": result.output,
            },
            "meta": {
                "tool_result_event_published": True,
            },
        }

    def _visible_tool_result_from_state(
        self,
        *,
        state: PersistedToolCallState | None,
        expected_tool_name: str,
    ) -> dict[str, JsonValue] | None:
        return self._tool_result_state_service().visible_tool_result_from_state(
            state=state,
            expected_tool_name=expected_tool_name,
            to_json_compatible=self._to_json_compatible,
        )

    @staticmethod
    def _visible_tool_result_from_envelope(
        result_envelope: dict[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        return ToolResultStateService().visible_tool_result_from_envelope(
            result_envelope
        )

    @staticmethod
    def _tool_result_event_was_published(
        *,
        result_envelope: dict[str, JsonValue],
        visible_result: dict[str, JsonValue] | None = None,
    ) -> bool:
        return ToolResultStateService().tool_result_event_was_published(
            result_envelope=result_envelope,
            visible_result=visible_result,
        )

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

    def _prompt_history_service(self) -> PromptHistoryService:
        mcp_tool_context_token_cache = getattr(
            self,
            "_mcp_tool_context_token_cache",
            {},
        )
        if not isinstance(mcp_tool_context_token_cache, dict):
            mcp_tool_context_token_cache = {}
        self._mcp_tool_context_token_cache = mcp_tool_context_token_cache
        return PromptHistoryService(
            config=getattr(
                self,
                "_config",
                ModelEndpointConfig(
                    model="gpt-test",
                    base_url="https://example.test/v1",
                    api_key="secret",
                ),
            ),
            run_intent_repo=cast(
                RunIntentRepository,
                getattr(self, "_run_intent_repo", _NullRunIntentRepo()),
            ),
            message_repo=getattr(
                self,
                "_message_repo",
                _NullPromptHistoryMessageRepo(),
            ),
            conversation_compaction_service=getattr(
                self,
                "_conversation_compaction_service",
                None,
            ),
            conversation_microcompact_service=getattr(
                self,
                "_conversation_microcompact_service",
                None,
            ),
            mcp_registry=getattr(self, "_mcp_registry", McpRegistry()),
            mcp_tool_context_token_cache=mcp_tool_context_token_cache,
            media_asset_service=getattr(self, "_media_asset_service", None),
            hook_service=getattr(self, "_hook_service", None),
            reminder_service=getattr(self, "_reminder_service", None),
            run_event_hub=getattr(self, "_run_event_hub", None),
            load_safe_history_for_conversation=getattr(
                self,
                "_load_safe_history_for_conversation",
                lambda _conversation_id: [],
            ),
        )

    def _attempt_recovery_service(self) -> AttemptRecoveryService:
        return AttemptRecoveryService(
            config=getattr(
                self,
                "_config",
                ModelEndpointConfig(
                    model="gpt-test",
                    base_url="https://example.test/v1",
                    api_key="secret",
                ),
            ),
            profile_name=getattr(self, "_profile_name", None),
            retry_config=getattr(self, "_retry_config", LlmRetryConfig()),
            fallback_middleware=getattr(self, "_fallback_middleware", None),
        )

    def _failure_handling_service(self) -> FailureHandlingService:
        return FailureHandlingService(
            config=getattr(
                self,
                "_config",
                ModelEndpointConfig(
                    model="gpt-test",
                    base_url="https://example.test/v1",
                    api_key="secret",
                ),
            ),
            profile_name=getattr(self, "_profile_name", None),
            retry_config=getattr(self, "_retry_config", LlmRetryConfig()),
            message_repo=cast(
                MessageRepository,
                getattr(self, "_message_repo", _NullPromptHistoryMessageRepo()),
            ),
            run_event_hub=getattr(self, "_run_event_hub", None),
        )

    def _stream_event_service(self) -> StreamEventService:
        return StreamEventService()

    def _tool_args_recovery_service(self) -> ToolArgsRecoveryService:
        return ToolArgsRecoveryService(
            message_repo=cast(
                MessageRepository,
                getattr(self, "_message_repo", _NullPromptHistoryMessageRepo()),
            ),
            stream_event_service=self._stream_event_service(),
        )

    def _event_publishing_service(self) -> EventPublishingService:
        return EventPublishingService(
            run_event_hub=getattr(self, "_run_event_hub", None),
        )

    def _message_commit_service(self) -> MessageCommitService:
        return MessageCommitService(
            message_repo=cast(
                MessageRepository,
                getattr(self, "_message_repo", _NullPromptHistoryMessageRepo()),
            ),
        )

    def _tool_result_state_service(self) -> ToolResultStateService:
        return ToolResultStateService()
