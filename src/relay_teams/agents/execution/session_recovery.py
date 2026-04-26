# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal, Protocol, cast

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from relay_teams.agents.execution.recovery_flow import (
    AttemptRecoveryOutcome,
    FallbackAttemptOutcome,
    FallbackAttemptState,
    FallbackAttemptStatus,
    RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
    RETRY_SUPERSEDED_TOOL_CALL_MESSAGE,
)
from relay_teams.agents.execution.session_mixin_base import AgentLlmSessionMixinBase
from relay_teams.logger import get_logger, log_event
from relay_teams.providers.llm_retry import LlmRetryErrorInfo, LlmRetrySchedule
from relay_teams.providers.model_config import ModelEndpointConfig
from relay_teams.providers.model_fallback import (
    DisabledLlmFallbackMiddleware,
    LlmFallbackDecision,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import build_tool_error_result
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import publish_run_event_async
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPauseError
from relay_teams.sessions.runs.run_models import RunEvent

LOGGER = get_logger(__name__)


class _SessionCloneFactory(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


class SessionRecoveryMixin(AgentLlmSessionMixinBase):
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
        await publish_run_event_async(
            self._run_event_hub,
            self._build_run_event(
                request=request,
                event_type="LLM_RETRY_SCHEDULED",
                payload=payload,
            ),
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
        fallback_state: FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> AttemptRecoveryOutcome:
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
            return AttemptRecoveryOutcome.recovered(recovered)
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
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
        )
        closed_pending_tool_call_count = 0
        if should_retry:
            closed_pending_tool_call_count = self._close_pending_tool_calls_for_retry(
                request=request,
                pending_messages=pending_messages,
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
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
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=should_resume_after_tool_outcomes,
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
            should_resume_after_tool_outcomes=should_resume_after_tool_outcomes,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
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
        fallback_state: FallbackAttemptState,
        skip_initial_user_prompt_persist: bool,
    ) -> AttemptRecoveryOutcome:
        return await self._attempt_recovery_service().execute_attempt_recovery(
            request=request,
            retry_error=retry_error,
            retry_number=retry_number,
            total_attempts=total_attempts,
            history=history,
            pending_messages=pending_messages,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=should_resume_after_tool_outcomes,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
            fallback_state=fallback_state,
            skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
            handle_retry_scheduled=self._handle_retry_scheduled,
            generate_async=self._generate_async,
            resume_after_tool_outcomes=self._resume_after_tool_outcomes,
            maybe_fallback_after_retry_exhausted=(
                self._maybe_fallback_after_retry_exhausted
            ),
        )

    async def _reset_cached_transport_for_retry(
        self,
        *,
        request: LLMRequest,
        retry_error: LlmRetryErrorInfo | None,
    ) -> None:
        await self._attempt_recovery_service().reset_cached_transport_for_retry(
            request=request,
            retry_error=retry_error,
        )

    async def _close_run_scoped_llm_http_client(
        self,
        *,
        request: LLMRequest,
    ) -> None:
        await self._attempt_recovery_service().close_run_scoped_llm_http_client(
            request=request
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
        self._failure_handling_service().log_generate_failure_diagnostics(
            request=request,
            error=error,
            retry_error=retry_error,
            diagnostics_kind=diagnostics_kind,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
            should_retry=should_retry,
            should_resume_after_tool_outcomes=should_resume_after_tool_outcomes,
            closed_pending_tool_call_count=closed_pending_tool_call_count,
            to_json_compatible=self._to_json_compatible,
            should_retry_after_text_side_effect=(
                self._should_retry_after_text_side_effect
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
            error_code=RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
            message=RETRY_SUPERSEDED_TOOL_CALL_MESSAGE,
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
        return self._attempt_recovery_service().should_retry_request(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
        )

    def _should_resume_after_tool_outcomes(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_tool_outcome_event_emitted: bool,
    ) -> bool:
        return self._attempt_recovery_service().should_resume_after_tool_outcomes(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
        )

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
        return await self._attempt_recovery_service().resume_after_tool_outcomes(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            history=history,
            pending_messages=pending_messages,
            fallback_state=fallback_state,
            restore_pending_tool_results_from_state=(
                self._restore_pending_tool_results_from_state
            ),
            commit_all_safe_messages=self._commit_all_safe_messages,
            publish_synthetic_tool_results_for_pending_calls=(
                self._publish_synthetic_tool_results_for_pending_calls
            ),
            generate_async=self._generate_async,
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
        fallback_status: FallbackAttemptStatus,
    ) -> None:
        self._failure_handling_service().raise_terminal_model_api_failure(
            request=request,
            error=error,
            retry_error=retry_error,
            retry_number=retry_number,
            total_attempts=total_attempts,
            error_message=error_message,
            fallback_status=fallback_status,
            handle_retry_exhausted=self._handle_retry_exhausted,
            raise_assistant_run_error=self._raise_assistant_run_error,
        )

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
        self._failure_handling_service().raise_terminal_generic_failure(
            request=request,
            error=error,
            retry_error=retry_error,
            retry_number=retry_number,
            total_attempts=total_attempts,
            fallback_status=fallback_status,
            log_provider_request_failed=self._log_provider_request_failed,
            handle_retry_exhausted=self._handle_retry_exhausted,
            raise_assistant_run_error=self._raise_assistant_run_error,
        )

    def _should_retry_after_text_side_effect(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
    ) -> bool:
        return self._attempt_recovery_service().should_retry_after_text_side_effect(
            retry_error=retry_error
        )

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
        return self._attempt_recovery_service().can_attempt_fallback(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
        )

    async def _maybe_fallback_after_retry_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        retry_error: LlmRetryErrorInfo,
        fallback_state: FallbackAttemptState,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        skip_initial_user_prompt_persist: bool,
    ) -> FallbackAttemptOutcome:
        return (
            await self._attempt_recovery_service().maybe_fallback_after_retry_exhausted(
                request=request,
                retry_number=retry_number,
                total_attempts=total_attempts,
                retry_error=retry_error,
                fallback_state=fallback_state,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
                attempt_messages_committed=attempt_messages_committed,
                skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
                clone_with_config=self._clone_with_config,
                handle_fallback_activated=self._handle_fallback_activated,
                handle_fallback_exhausted=self._handle_fallback_exhausted,
            )
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
        return self._failure_handling_service().build_recoverable_pause_error(
            request=request,
            error=error,
            retry_number=retry_number,
            total_attempts=total_attempts,
            error_message=error_message,
        )

    def _raise_assistant_run_error(
        self,
        *,
        request: LLMRequest,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        self._failure_handling_service().raise_assistant_run_error(
            request=request,
            error_code=error_code,
            error_message=error_message,
            publish_text_delta_event=self._publish_text_delta_event,
            conversation_id=self._conversation_id,
            workspace_id=self._workspace_id,
        )

    def _handle_retry_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
    ) -> None:
        self._failure_handling_service().handle_retry_exhausted(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            error=error,
        )

    def _handle_fallback_activated(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        decision: LlmFallbackDecision,
    ) -> None:
        self._failure_handling_service().handle_fallback_activated(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            decision=decision,
        )

    def _handle_fallback_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
        fallback_state: FallbackAttemptState,
    ) -> None:
        self._failure_handling_service().handle_fallback_exhausted(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            error=error,
            fallback_state=fallback_state,
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

    def _build_run_event(
        self,
        *,
        request: LLMRequest,
        event_type: str,
        payload: dict[str, object],
    ) -> RunEvent:
        return RunEvent(
            session_id=request.session_id,
            run_id=request.run_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            event_type=getattr(RunEventType, event_type),
            payload_json=self._to_json(payload),
        )

    def _clone_with_config(
        self,
        *,
        config: ModelEndpointConfig,
        profile_name: str | None,
    ) -> object:
        session_factory = cast(_SessionCloneFactory, self.__class__)
        return session_factory(
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
