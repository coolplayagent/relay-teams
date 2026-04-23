# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from json import dumps
from typing import Protocol, cast

from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart

from relay_teams.agents.execution.recovery_flow import (
    FallbackAttemptState,
    FallbackAttemptStatus,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.providers.llm_retry import LlmRetryErrorInfo
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.model_fallback import LlmFallbackDecision
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import (
    AssistantRunError,
    AssistantRunErrorPayload,
    build_assistant_error_message,
    build_assistant_error_response,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)

LOGGER = get_logger(__name__)


class FailureMessageRepository(Protocol):
    def prune_conversation_history_to_safe_boundary(
        self,
        conversation_id: str,
    ) -> None: ...

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
        messages: list[ModelResponse],
    ) -> None: ...


class RunEventPublisher(Protocol):
    def publish(self, event: RunEvent) -> None: ...


class FailureHandlingService:
    def __init__(
        self,
        *,
        config: ModelEndpointConfig,
        profile_name: str | None,
        retry_config: LlmRetryConfig,
        message_repo: FailureMessageRepository,
        run_event_hub: RunEventPublisher | None,
    ) -> None:
        self._config = config
        self._profile_name = (
            profile_name.strip()
            if profile_name is not None and profile_name.strip()
            else None
        )
        self._retry_config = retry_config
        self._message_repo = message_repo
        self._run_event_hub = run_event_hub

    def build_recoverable_pause_error(
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

    def raise_assistant_run_error(
        self,
        *,
        request: LLMRequest,
        error_code: str | None,
        error_message: str | None,
        publish_text_delta_event: Callable[..., None],
        conversation_id: Callable[[LLMRequest], str],
        workspace_id: Callable[[LLMRequest], str],
    ) -> None:
        assistant_message = build_assistant_error_message(
            error_code=error_code,
            error_message=error_message,
        )
        resolved_conversation_id = conversation_id(request)
        self._message_repo.prune_conversation_history_to_safe_boundary(
            resolved_conversation_id
        )
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=workspace_id(request),
            conversation_id=resolved_conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[build_assistant_error_response(assistant_message)],
        )
        publish_text_delta_event(request=request, text=assistant_message)
        raise AssistantRunError(
            AssistantRunErrorPayload(
                trace_id=request.trace_id,
                session_id=request.session_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                conversation_id=resolved_conversation_id,
                assistant_message=assistant_message,
                error_code=str(error_code or ""),
                error_message=str(error_message or ""),
            )
        )

    def handle_retry_exhausted(
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
        self._publish_run_event(
            request=request,
            event_type=RunEventType.LLM_RETRY_EXHAUSTED,
            payload=payload,
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

    def handle_fallback_activated(
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
        self._publish_run_event(
            request=request,
            event_type=RunEventType.LLM_FALLBACK_ACTIVATED,
            payload=payload,
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.fallback_activated",
            message="LLM request fallback activated after rate limit exhaustion",
            payload=payload,
        )

    def handle_fallback_exhausted(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        error: LlmRetryErrorInfo,
        fallback_state: FallbackAttemptState,
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
        self._publish_run_event(
            request=request,
            event_type=RunEventType.LLM_FALLBACK_EXHAUSTED,
            payload=payload,
        )
        log_event(
            LOGGER,
            logging.ERROR,
            event="llm.request.fallback_exhausted",
            message="No fallback candidate succeeded after LLM rate limit exhaustion",
            payload=payload,
        )

    def raise_terminal_model_api_failure(
        self,
        *,
        request: LLMRequest,
        error: ModelAPIError,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        error_message: str,
        fallback_status: FallbackAttemptStatus,
        handle_retry_exhausted: Callable[..., None],
        raise_assistant_run_error: Callable[..., None],
    ) -> None:
        if retry_error is not None and retry_error.retryable:
            if (
                fallback_status != FallbackAttemptStatus.EXHAUSTED
                and self._retry_config.enabled
                and retry_number >= self._retry_config.max_retries
            ):
                handle_retry_exhausted(
                    request=request,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    error=retry_error,
                )
            raise_assistant_run_error(
                request=request,
                error_code=retry_error.error_code,
                error_message=error_message,
            )
        raise_assistant_run_error(
            request=request,
            error_code=(
                retry_error.error_code
                if retry_error is not None
                else getattr(error, "model_name", None)
            ),
            error_message=error_message,
        )

    def raise_terminal_generic_failure(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        total_attempts: int,
        fallback_status: FallbackAttemptStatus,
        log_provider_request_failed: Callable[..., None],
        handle_retry_exhausted: Callable[..., None],
        raise_assistant_run_error: Callable[..., None],
    ) -> None:
        if retry_error is not None:
            log_provider_request_failed(request=request, error=error)
            if retry_error.retryable and (
                fallback_status != FallbackAttemptStatus.EXHAUSTED
                and self._retry_config.enabled
                and retry_number >= self._retry_config.max_retries
            ):
                handle_retry_exhausted(
                    request=request,
                    retry_number=retry_number,
                    total_attempts=total_attempts,
                    error=retry_error,
                )
            raise_assistant_run_error(
                request=request,
                error_code=retry_error.error_code,
                error_message=retry_error.message,
            )
        raise_assistant_run_error(
            request=request,
            error_code="internal_execution_error",
            error_message=str(error) or error.__class__.__name__,
        )

    def log_generate_failure_diagnostics(
        self,
        *,
        request: LLMRequest,
        error: BaseException,
        retry_error: LlmRetryErrorInfo | None,
        diagnostics_kind: str,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_call_event_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        should_retry: bool,
        should_resume_after_tool_outcomes: bool,
        closed_pending_tool_call_count: int,
        to_json_compatible: Callable[[object], JsonValue],
        should_retry_after_text_side_effect: Callable[..., bool],
    ) -> None:
        if diagnostics_kind == "model_api_error":
            assert isinstance(error, ModelAPIError)
            event = "llm.request.model_api_error.diagnostics"
            message = "ModelAPIError retry diagnostics"
            payload = self.model_api_error_diagnostics_payload(
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
                to_json_compatible=to_json_compatible,
                should_retry_after_text_side_effect=(
                    should_retry_after_text_side_effect
                ),
            )
        else:
            event = "llm.request.exception.diagnostics"
            message = "Unhandled exception retry diagnostics"
            payload = self.exception_retry_diagnostics_payload(
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
                to_json_compatible=to_json_compatible,
                should_retry_after_text_side_effect=(
                    should_retry_after_text_side_effect
                ),
            )
        log_event(
            LOGGER,
            logging.ERROR,
            event=event,
            message=message,
            payload=cast(dict[str, JsonValue], to_json_compatible(payload)),
        )

    def build_model_api_error_message(self, error: ModelAPIError) -> str:
        chain = self.exception_chain(error)
        if self.is_proxy_auth_failure(chain):
            return (
                f"{error.message} Proxy authentication failed (HTTP 407). "
                "Check HTTP_PROXY/HTTPS_PROXY credentials or set NO_PROXY for the model endpoint."
            )
        if self.is_connect_timeout(chain):
            return (
                f"{error.message} Connection to the model endpoint timed out. "
                "Check base_url, proxy/NO_PROXY settings, network reachability, "
                "or increase connect_timeout_seconds in the model profile."
            )

        root_message = self.deepest_distinct_exception_message(
            chain=chain,
            primary_message=error.message,
        )
        if root_message is None:
            return error.message
        return f"{error.message} Root cause: {root_message}"

    def model_api_error_diagnostics_payload(
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
        to_json_compatible: Callable[[object], JsonValue],
        should_retry_after_text_side_effect: Callable[..., bool],
    ) -> dict[str, object]:
        chain = self.exception_chain(error)
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
            "body": self.diagnostic_value(
                getattr(error, "body", None),
                to_json_compatible=to_json_compatible,
            ),
            "headers": self.diagnostic_headers(direct_headers),
            "response_headers": self.diagnostic_headers(response_headers),
            "exception_chain": [
                self.exception_diagnostic_item(
                    item,
                    to_json_compatible=to_json_compatible,
                )
                for item in chain
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
            "tool_event_state": self.tool_event_state(
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            ),
            "attempt_messages_committed": attempt_messages_committed,
            "should_retry": should_retry,
            "should_resume_after_tool_outcomes": should_resume_after_tool_outcomes,
            "closed_pending_tool_call_count": closed_pending_tool_call_count,
            "retry_blockers": self.retry_blockers(
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
                attempt_messages_committed=attempt_messages_committed,
                should_retry_after_text_side_effect=(
                    should_retry_after_text_side_effect
                ),
            ),
        }

    def exception_retry_diagnostics_payload(
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
        to_json_compatible: Callable[[object], JsonValue],
        should_retry_after_text_side_effect: Callable[..., bool],
    ) -> dict[str, object]:
        chain = self.exception_chain(error)
        return {
            "error_type": error.__class__.__name__,
            "message": str(error),
            "args": [
                self.diagnostic_value(item, to_json_compatible=to_json_compatible)
                for item in error.args
            ],
            "exception_chain": [
                self.exception_diagnostic_item(
                    item,
                    to_json_compatible=to_json_compatible,
                )
                for item in chain
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
            "tool_event_state": self.tool_event_state(
                attempt_tool_call_event_emitted=attempt_tool_call_event_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
            ),
            "attempt_messages_committed": attempt_messages_committed,
            "should_retry": should_retry,
            "should_resume_after_tool_outcomes": should_resume_after_tool_outcomes,
            "closed_pending_tool_call_count": closed_pending_tool_call_count,
            "retry_blockers": self.retry_blockers(
                retry_error=retry_error,
                retry_number=retry_number,
                attempt_text_emitted=attempt_text_emitted,
                attempt_tool_outcome_event_emitted=attempt_tool_outcome_event_emitted,
                attempt_messages_committed=attempt_messages_committed,
                should_retry_after_text_side_effect=(
                    should_retry_after_text_side_effect
                ),
            ),
        }

    def exception_chain(self, error: BaseException) -> tuple[BaseException, ...]:
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

    def is_proxy_auth_failure(self, chain: Sequence[BaseException]) -> bool:
        for error in chain:
            message = str(error).strip().lower()
            if "407 proxy authentication required" in message:
                return True
        return False

    def is_connect_timeout(self, chain: Sequence[BaseException]) -> bool:
        for error in chain:
            if error.__class__.__name__ == "ConnectTimeout":
                return True
        return False

    def deepest_distinct_exception_message(
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

    def exception_diagnostic_item(
        self,
        error: BaseException,
        *,
        to_json_compatible: Callable[[object], JsonValue],
    ) -> dict[str, object]:
        response = getattr(error, "response", None)
        return {
            "type": error.__class__.__name__,
            "message": str(error),
            "status_code": getattr(error, "status_code", None),
            "code": getattr(error, "code", None),
            "body": self.diagnostic_value(
                getattr(error, "body", None),
                to_json_compatible=to_json_compatible,
            ),
            "response_headers": self.diagnostic_headers(
                getattr(response, "headers", None)
            ),
        }

    def diagnostic_value(
        self,
        value: object,
        *,
        to_json_compatible: Callable[[object], JsonValue],
    ) -> object:
        compatible = to_json_compatible(value)
        serialized = json.dumps(compatible, ensure_ascii=False, default=str)
        if len(serialized) <= 1_500:
            return compatible
        return f"{serialized[:1500]}...<truncated>"

    def diagnostic_headers(self, headers: object) -> dict[str, str]:
        header_names = (
            "retry-after",
            "x-should-retry",
            "x-request-id",
            "request-id",
            "content-type",
        )
        values: dict[str, str] = {}
        for name in header_names:
            value = self.header_value(headers, name)
            if value:
                values[name] = value
        return values

    def header_value(self, headers: object, name: str) -> str:
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

    def tool_event_state(
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

    def retry_blockers(
        self,
        *,
        retry_error: LlmRetryErrorInfo | None,
        retry_number: int,
        attempt_text_emitted: bool,
        attempt_tool_outcome_event_emitted: bool,
        attempt_messages_committed: bool,
        should_retry_after_text_side_effect: Callable[..., bool],
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
        if attempt_text_emitted and not should_retry_after_text_side_effect(
            retry_error=retry_error
        ):
            blockers.append("text_already_emitted")
        if attempt_tool_outcome_event_emitted:
            blockers.append("tool_outcomes_emitted")
        if attempt_messages_committed:
            blockers.append("messages_already_committed")
        return tuple(blockers)

    def extract_text(self, response: object) -> str:
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

    def apply_streamed_text_fallback(
        self,
        messages: list[ModelRequest | ModelResponse],
        *,
        streamed_text: str,
        replace_message: Callable[..., ModelResponse],
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
            existing_text = self.extract_text(message)
            if existing_text == streamed_text:
                return updated_messages
            next_parts = []
            fallback_inserted = False
            for part in message.parts:
                if isinstance(part, TextPart):
                    if fallback_inserted:
                        continue
                    next_parts.append(TextPart(content=streamed_text))
                    fallback_inserted = True
                    continue
                next_parts.append(part)
            updated_messages[index] = replace_message(message=message, parts=next_parts)
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

    def _publish_run_event(
        self,
        *,
        request: LLMRequest,
        event_type: RunEventType,
        payload: dict[str, object],
    ) -> None:
        if self._run_event_hub is None:
            return
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=event_type,
                payload_json=dumps(payload),
            )
        )
