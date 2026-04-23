# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

from relay_teams.logger import get_logger, log_event
from relay_teams.net.llm_client import reset_llm_http_client_cache_entry
from relay_teams.providers.llm_retry import (
    LlmRetryErrorInfo,
    LlmRetrySchedule,
    compute_retry_delay_ms,
)
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.model_fallback import (
    DisabledLlmFallbackMiddleware,
    LlmFallbackMiddleware,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import build_tool_error_result

LOGGER = get_logger(__name__)
RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE = "tool_call_superseded_by_retry"
RETRY_SUPERSEDED_TOOL_CALL_MESSAGE = "This tool call was superseded by an automatic model retry before tool execution started."
RESUME_SUPERSEDED_TOOL_CALL_ERROR_CODE = "tool_call_superseded_by_resume"
RESUME_SUPERSEDED_TOOL_CALL_MESSAGE = (
    "This tool call was superseded by automatic recovery after a model request failure."
)


class FallbackAttemptState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hop: int = Field(default=0, ge=0)
    visited_profiles: tuple[str, ...] = ()

    @classmethod
    def initial(cls, profile_name: str | None) -> "FallbackAttemptState":
        if profile_name is None or not profile_name.strip():
            return cls()
        return cls(visited_profiles=(profile_name.strip(),))

    def with_profile(self, profile_name: str, *, hop: int) -> "FallbackAttemptState":
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


class FallbackAttemptStatus(StrEnum):
    SKIPPED = "skipped"
    RECOVERED = "recovered"
    EXHAUSTED = "exhausted"


class FallbackAttemptOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: FallbackAttemptStatus
    response: str | None = None

    @classmethod
    def skipped(cls) -> "FallbackAttemptOutcome":
        return cls(status=FallbackAttemptStatus.SKIPPED)

    @classmethod
    def exhausted(cls) -> "FallbackAttemptOutcome":
        return cls(status=FallbackAttemptStatus.EXHAUSTED)

    @classmethod
    def recovered(cls, response: str) -> "FallbackAttemptOutcome":
        return cls(
            status=FallbackAttemptStatus.RECOVERED,
            response=response,
        )


class AttemptRecoveryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: str | None = None
    fallback_status: FallbackAttemptStatus = FallbackAttemptStatus.SKIPPED

    @classmethod
    def no_recovery(cls) -> "AttemptRecoveryOutcome":
        return cls()

    @classmethod
    def recovered(
        cls,
        response: str,
        *,
        fallback_status: FallbackAttemptStatus = FallbackAttemptStatus.SKIPPED,
    ) -> "AttemptRecoveryOutcome":
        return cls(
            response=response,
            fallback_status=fallback_status,
        )

    @classmethod
    def fallback_exhausted(cls) -> "AttemptRecoveryOutcome":
        return cls(fallback_status=FallbackAttemptStatus.EXHAUSTED)


class _GeneratedSession(Protocol):
    async def _generate_async(
        self,
        request: LLMRequest,
        *,
        retry_number: int = 0,
        total_attempts: int | None = None,
        skip_initial_user_prompt_persist: bool = False,
        fallback_state: FallbackAttemptState | None = None,
    ) -> str: ...


class AttemptRecoveryService:
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        profile_name: str | None,
        retry_config: LlmRetryConfig,
        fallback_middleware: LlmFallbackMiddleware
        | DisabledLlmFallbackMiddleware
        | None = None,
    ) -> None:
        self._config = config
        self._profile_name = (
            profile_name.strip()
            if profile_name is not None and profile_name.strip()
            else None
        )
        self._retry_config = retry_config
        self._fallback_middleware = (
            fallback_middleware
            if fallback_middleware is not None
            else DisabledLlmFallbackMiddleware()
        )

    async def execute_attempt_recovery(
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
        handle_retry_scheduled: Callable[..., Awaitable[None]],
        generate_async: Callable[..., Awaitable[str]],
        resume_after_tool_outcomes: Callable[..., Awaitable[str]],
        maybe_fallback_after_retry_exhausted: Callable[
            ...,
            Awaitable[FallbackAttemptOutcome],
        ],
    ) -> AttemptRecoveryOutcome:
        if should_retry:
            await self.reset_cached_transport_for_retry(
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
            await handle_retry_scheduled(
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
            return AttemptRecoveryOutcome.recovered(
                await generate_async(
                    request,
                    retry_number=next_retry_number,
                    total_attempts=total_attempts,
                    fallback_state=fallback_state,
                )
            )
        if should_resume_after_tool_outcomes:
            await self.reset_cached_transport_for_retry(
                request=request,
                retry_error=retry_error,
            )
            return AttemptRecoveryOutcome.recovered(
                await resume_after_tool_outcomes(
                    request=request,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    history=history,
                    pending_messages=pending_messages,
                    fallback_state=fallback_state,
                )
            )
        if retry_error is not None:
            fallback_outcome = await maybe_fallback_after_retry_exhausted(
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
            )
            if fallback_outcome.response is not None:
                return AttemptRecoveryOutcome.recovered(
                    fallback_outcome.response,
                    fallback_status=fallback_outcome.status,
                )
            if fallback_outcome.status == FallbackAttemptStatus.EXHAUSTED:
                return AttemptRecoveryOutcome.fallback_exhausted()
        return AttemptRecoveryOutcome.no_recovery()

    async def reset_cached_transport_for_retry(
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

    async def close_run_scoped_llm_http_client(
        self,
        *,
        request: LLMRequest,
    ) -> None:
        await reset_llm_http_client_cache_entry(
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            cache_scope=request.run_id,
        )

    def should_retry_request(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
    ) -> bool:
        allow_after_text = self.should_retry_after_text_side_effect(
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

    def should_resume_after_tool_outcomes(
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

    async def resume_after_tool_outcomes(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        fallback_state: FallbackAttemptState,
        restore_pending_tool_results_from_state: Callable[
            ...,
            Awaitable[tuple[list[ModelRequest | ModelResponse], int]],
        ],
        commit_all_safe_messages: Callable[
            ...,
            tuple[
                list[ModelRequest | ModelResponse],
                list[ModelRequest | ModelResponse],
                bool,
                bool,
            ],
        ],
        publish_synthetic_tool_results_for_pending_calls: Callable[..., int],
        generate_async: Callable[..., Awaitable[str]],
    ) -> str:
        next_retry_number = retry_number + 1
        (
            recovered_pending_messages,
            recovered_tool_result_count,
        ) = await restore_pending_tool_results_from_state(
            request=request,
            pending_messages=pending_messages,
        )
        (
            next_history,
            remaining_pending_messages,
            _committed_tool_events_published,
            _committed_tool_validation_failures,
        ) = commit_all_safe_messages(
            request=request,
            history=history,
            pending_messages=recovered_pending_messages,
        )
        closed_pending_tool_call_count = (
            publish_synthetic_tool_results_for_pending_calls(
                request=request,
                pending_messages=remaining_pending_messages,
                error_code=RESUME_SUPERSEDED_TOOL_CALL_ERROR_CODE,
                message=RESUME_SUPERSEDED_TOOL_CALL_MESSAGE,
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
        return await generate_async(
            request,
            retry_number=next_retry_number,
            total_attempts=total_attempts,
            skip_initial_user_prompt_persist=True,
            fallback_state=fallback_state,
        )

    def should_retry_after_text_side_effect(
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

    def can_attempt_fallback(
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

    async def maybe_fallback_after_retry_exhausted(
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
        clone_with_config: Callable[..., object],
        handle_fallback_activated: Callable[..., None],
        handle_fallback_exhausted: Callable[..., None],
    ) -> FallbackAttemptOutcome:
        if not self.can_attempt_fallback(
            retry_error=retry_error,
            retry_number=retry_number,
            attempt_text_emitted=attempt_text_emitted,
            attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
            attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            attempt_messages_committed=attempt_messages_committed,
        ):
            return FallbackAttemptOutcome.skipped()
        if not self._fallback_middleware.has_enabled_policy(self._config):
            return FallbackAttemptOutcome.skipped()
        decision = self._fallback_middleware.select_fallback(
            current_profile_name=self._profile_name,
            current_config=self._config,
            error=retry_error,
            visited_profiles=fallback_state.visited_profiles,
            hop=fallback_state.hop,
        )
        if decision is None:
            handle_fallback_exhausted(
                request=request,
                retry_number=retry_number,
                total_attempts=total_attempts,
                error=retry_error,
                fallback_state=fallback_state,
            )
            return FallbackAttemptOutcome.exhausted()
        handle_fallback_activated(
            request=request,
            retry_number=retry_number,
            total_attempts=total_attempts,
            decision=decision,
        )
        next_session = cast(
            _GeneratedSession,
            clone_with_config(
                config=decision.target_config,
                profile_name=decision.to_profile_name,
            ),
        )
        next_fallback_state = fallback_state.with_profile(
            decision.to_profile_name,
            hop=decision.hop,
        )
        return FallbackAttemptOutcome.recovered(
            await next_session._generate_async(
                request,
                retry_number=0,
                total_attempts=None,
                skip_initial_user_prompt_persist=skip_initial_user_prompt_persist,
                fallback_state=next_fallback_state,
            )
        )

    def publish_synthetic_tool_results_for_pending_calls(
        self,
        *,
        pending_tool_calls: Sequence[tuple[str, str]],
    ) -> ModelRequest | None:
        if not pending_tool_calls:
            return None
        return ModelRequest(
            parts=[
                build_tool_error_result_part(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error_code=RETRY_SUPERSEDED_TOOL_CALL_ERROR_CODE,
                    message=RETRY_SUPERSEDED_TOOL_CALL_MESSAGE,
                )
                for tool_call_id, tool_name in pending_tool_calls
            ]
        )


def build_tool_error_result_part(
    *,
    tool_call_id: str,
    tool_name: str,
    error_code: str,
    message: str,
):
    return ToolReturnPart(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        content=build_tool_error_result(
            error_code=error_code,
            message=message,
        ),
    )
