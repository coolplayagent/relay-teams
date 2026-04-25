# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from relay_teams.agents.execution import session_support as session_support_module

from .agent_llm_session_test_support import (
    AgentLlmSession,
    AssistantRunError,
    LLMRequest,
    LlmFallbackDecision,
    LlmRetryConfig,
    LlmRetryErrorInfo,
    LlmRetrySchedule,
    ModelAPIError,
    ModelEndpointConfig,
    ModelRequest,
    ModelResponse,
    PersistedToolCallState,
    ToolCallPart,
    ToolExecutionStatus,
    ToolReturnPart,
    _FallbackAttemptState,
    _FallbackAttemptStatus,
    _build_request,
    recovery_module,
)

EXPECTED_LLM_HTTP_CLIENT_CACHE_SCOPE = "run-1:session-1:task-1:inst-1:writer"


@pytest.mark.asyncio
async def test_execute_attempt_recovery_clears_cached_transport_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=1,
        initial_delay_ms=1,
    )
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )

    cleared: list[str] = []

    async def _reset_llm_http_client_cache_entry(**kwargs: object) -> None:
        assert kwargs["cache_scope"] == EXPECTED_LLM_HTTP_CLIENT_CACHE_SCOPE
        cleared.append("cleared")

    monkeypatch.setattr(
        recovery_module,
        "reset_llm_http_client_cache_entry",
        _reset_llm_http_client_cache_entry,
    )
    monkeypatch.setattr(recovery_module, "compute_retry_delay_ms", lambda **_: 0)

    async def _fast_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(recovery_module.asyncio, "sleep", _fast_sleep)

    scheduled: list[LlmRetrySchedule] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        scheduled.append(cast(LlmRetrySchedule, kwargs["schedule"]))

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled

    async def _generate_async(
        request: LLMRequest,
        **kwargs: object,
    ) -> str:
        _ = (request, kwargs)
        assert cleared == ["cleared"]
        return "after retry"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=LlmRetryErrorInfo(
            message="TLS handshake failed",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        ),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[],
        should_retry=True,
        should_resume_after_tool_outcomes=False,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "after retry"
    assert scheduled[0].delay_ms == 0
    assert cleared == ["cleared"]


@pytest.mark.asyncio
async def test_execute_attempt_recovery_keeps_cached_transport_for_non_transport_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=1,
        initial_delay_ms=1,
    )
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )

    cleared: list[str] = []

    async def _reset_llm_http_client_cache_entry(**kwargs: object) -> None:
        assert kwargs["cache_scope"] == EXPECTED_LLM_HTTP_CLIENT_CACHE_SCOPE
        cleared.append("cleared")

    monkeypatch.setattr(
        recovery_module,
        "reset_llm_http_client_cache_entry",
        _reset_llm_http_client_cache_entry,
    )
    monkeypatch.setattr(recovery_module, "compute_retry_delay_ms", lambda **_: 0)

    async def _fast_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(recovery_module.asyncio, "sleep", _fast_sleep)

    async def _ignore_retry_schedule(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_handle_retry_scheduled"] = _ignore_retry_schedule

    async def _generate_async(
        request: LLMRequest,
        **kwargs: object,
    ) -> str:
        _ = (request, kwargs)
        assert cleared == []
        return "after retry"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=LlmRetryErrorInfo(
            message="slow down",
            status_code=429,
            error_code="rate_limited",
            retryable=True,
            rate_limited=True,
            transport_error=False,
        ),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[],
        should_retry=True,
        should_resume_after_tool_outcomes=False,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "after retry"
    assert cleared == []


@pytest.mark.asyncio
async def test_execute_attempt_recovery_clears_cached_transport_before_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=1)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )

    cleared: list[str] = []

    async def _reset_llm_http_client_cache_entry(**kwargs: object) -> None:
        assert kwargs["cache_scope"] == EXPECTED_LLM_HTTP_CLIENT_CACHE_SCOPE
        cleared.append("cleared")

    monkeypatch.setattr(
        recovery_module,
        "reset_llm_http_client_cache_entry",
        _reset_llm_http_client_cache_entry,
    )

    async def _resume_after_tool_outcomes(**kwargs: object) -> str:
        _ = kwargs
        assert cleared == ["cleared"]
        return "resumed"

    session.__dict__["_resume_after_tool_outcomes"] = _resume_after_tool_outcomes

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=LlmRetryErrorInfo(
            message="TLS handshake failed",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        ),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[],
        should_retry=False,
        should_resume_after_tool_outcomes=True,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "resumed"
    assert cleared == ["cleared"]


def test_should_retry_request_allows_transport_retry_after_text_side_effect() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=2)

    should_retry = AgentLlmSession._should_retry_request(
        session,
        retry_error=LlmRetryErrorInfo(
            message="connection reset",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        ),
        retry_number=0,
        attempt_text_emitted=True,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
    )

    assert should_retry is True


def test_should_retry_request_blocks_non_transport_retry_after_text_side_effect() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=2)

    should_retry = AgentLlmSession._should_retry_request(
        session,
        retry_error=LlmRetryErrorInfo(
            message="bad request",
            status_code=400,
            error_code="invalid_request",
            retryable=True,
            transport_error=False,
        ),
        retry_number=0,
        attempt_text_emitted=True,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
    )

    assert should_retry is False


def test_can_attempt_fallback_blocks_when_attempt_already_emitted_tool_events() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)

    can_fallback = AgentLlmSession._can_attempt_fallback(
        session,
        retry_error=LlmRetryErrorInfo(
            message="slow down",
            status_code=429,
            error_code="rate_limited",
            retryable=True,
            rate_limited=True,
        ),
        retry_number=0,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=True,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
    )

    assert can_fallback is False


@pytest.mark.asyncio
async def test_execute_attempt_recovery_returns_no_recovery_without_retry_resume_or_fallback() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=None,
        retry_number=0,
        total_attempts=1,
        history=[],
        pending_messages=[],
        should_retry=False,
        should_resume_after_tool_outcomes=False,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response is None
    assert result.fallback_status == _FallbackAttemptStatus.SKIPPED


@pytest.mark.asyncio
async def test_resume_after_tool_outcomes_commits_backfilled_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    persisted_state = PersistedToolCallState(
        tool_call_id="call-dispatch-1",
        tool_name="orch_dispatch_task",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "visible_result": {
                "ok": True,
                "data": {"task": {"task_id": "task-child-1", "status": "completed"}},
                "meta": {"tool_result_event_published": True},
            },
            "runtime_meta": {"tool_result_event_published": True},
        },
    )

    monkeypatch.setattr(
        session_support_module,
        "load_or_recover_tool_call_state",
        lambda **kwargs: persisted_state,
    )

    captured_pending_messages: list[ModelRequest | ModelResponse] = []

    def _capture_commit_all_safe_messages(**kwargs: object):
        pending_messages = kwargs["pending_messages"]
        assert isinstance(pending_messages, list)
        captured_pending_messages.extend(
            cast(list[ModelRequest | ModelResponse], pending_messages)
        )
        synthetic_request = cast(
            ModelRequest,
            cast(list[ModelRequest | ModelResponse], pending_messages)[-1],
        )
        recovered_part = synthetic_request.parts[0]
        assert isinstance(recovered_part, ToolReturnPart)
        assert recovered_part.tool_call_id == "call-dispatch-1"
        return [], [], False, False

    session.__dict__["_commit_all_safe_messages"] = _capture_commit_all_safe_messages
    session.__dict__["_publish_synthetic_tool_results_for_pending_calls"] = (
        lambda **kwargs: 0
    )

    async def _generate_async(*args: object, **kwargs: object) -> str:
        _ = (args, kwargs)
        return "resumed"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._resume_after_tool_outcomes(
        session,
        request=_build_request(),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="orch_dispatch_task",
                        args='{"task_id":"task-child-1","role_id":"Crafter"}',
                        tool_call_id="call-dispatch-1",
                    )
                ]
            )
        ],
        fallback_state=_FallbackAttemptState.initial("default"),
    )

    assert result == "resumed"
    assert len(captured_pending_messages) == 2


@pytest.mark.asyncio
async def test_maybe_fallback_after_retry_exhausted_switches_profile() -> None:
    session = object.__new__(AgentLlmSession)
    primary_config = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    fallback_config = ModelEndpointConfig(
        model="fallback-model",
        base_url="https://fallback.test/v1",
        api_key="fallback-key",
        fallback_priority=10,
    )
    session.__dict__["_config"] = primary_config
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=1)

    class _FallbackMiddleware:
        def has_enabled_policy(self, config: ModelEndpointConfig) -> bool:
            return config.fallback_policy_id == "same_provider_then_other_provider"

        def select_fallback(self, **kwargs: object) -> LlmFallbackDecision:
            _ = kwargs
            return LlmFallbackDecision(
                policy_id="same_provider_then_other_provider",
                from_profile_name="primary",
                to_profile_name="secondary",
                from_provider=primary_config.provider,
                to_provider=fallback_config.provider,
                from_model=primary_config.model,
                to_model=fallback_config.model,
                hop=1,
                reason="rate_limited",
                cooldown_until=datetime.now(UTC),
                target_config=fallback_config,
            )

    activated: list[LlmFallbackDecision] = []
    session.__dict__["_fallback_middleware"] = _FallbackMiddleware()
    session.__dict__["_handle_fallback_activated"] = lambda **kwargs: activated.append(
        cast(LlmFallbackDecision, kwargs["decision"])
    )
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: None

    captured_generate_kwargs: list[dict[str, object]] = []

    class _FallbackSession:
        async def _generate_async(
            self,
            request: LLMRequest,
            **kwargs: object,
        ) -> str:
            _ = request
            captured_generate_kwargs.append(kwargs)
            return "fallback-response"

    session.__dict__["_clone_with_config"] = lambda **kwargs: _FallbackSession()

    result = await AgentLlmSession._maybe_fallback_after_retry_exhausted(
        session,
        request=_build_request(),
        retry_number=1,
        total_attempts=2,
        retry_error=LlmRetryErrorInfo(
            message="slow down",
            status_code=429,
            error_code="rate_limited",
            retryable=True,
            rate_limited=True,
        ),
        fallback_state=_FallbackAttemptState.initial("primary"),
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "fallback-response"
    assert result.status == _FallbackAttemptStatus.RECOVERED
    assert len(activated) == 1
    assert activated[0].to_profile_name == "secondary"
    assert captured_generate_kwargs[0]["retry_number"] == 0
    next_fallback_state = captured_generate_kwargs[0]["fallback_state"]
    assert getattr(next_fallback_state, "hop") == 1


@pytest.mark.asyncio
async def test_maybe_fallback_after_non_retryable_quota_error_switches_profile() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    primary_config = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    fallback_config = ModelEndpointConfig(
        model="fallback-model",
        base_url="https://fallback.test/v1",
        api_key="fallback-key",
        fallback_priority=10,
    )
    session.__dict__["_config"] = primary_config
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=3)

    class _FallbackMiddleware:
        def has_enabled_policy(self, config: ModelEndpointConfig) -> bool:
            return config.fallback_policy_id == "same_provider_then_other_provider"

        def select_fallback(self, **kwargs: object) -> LlmFallbackDecision:
            _ = kwargs
            return LlmFallbackDecision(
                policy_id="same_provider_then_other_provider",
                from_profile_name="primary",
                to_profile_name="secondary",
                from_provider=primary_config.provider,
                to_provider=fallback_config.provider,
                from_model=primary_config.model,
                to_model=fallback_config.model,
                hop=1,
                reason="insufficient_quota",
                cooldown_until=datetime.now(UTC),
                target_config=fallback_config,
            )

    session.__dict__["_fallback_middleware"] = _FallbackMiddleware()
    session.__dict__["_handle_fallback_activated"] = lambda **kwargs: None
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: None

    class _FallbackSession:
        async def _generate_async(
            self,
            request: LLMRequest,
            **kwargs: object,
        ) -> str:
            _ = (request, kwargs)
            return "fallback-response"

    session.__dict__["_clone_with_config"] = lambda **kwargs: _FallbackSession()

    result = await AgentLlmSession._maybe_fallback_after_retry_exhausted(
        session,
        request=_build_request(),
        retry_number=0,
        total_attempts=4,
        retry_error=LlmRetryErrorInfo(
            message="quota exceeded",
            status_code=400,
            error_code="insufficient_quota",
            retryable=False,
            rate_limited=True,
        ),
        fallback_state=_FallbackAttemptState.initial("primary"),
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "fallback-response"
    assert result.status == _FallbackAttemptStatus.RECOVERED


def test_raise_assistant_run_error_persists_error_response_and_publishes_delta() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    from .agent_llm_session_test_support import MessageRepository, _FakeMessageRepo

    message_repo = _FakeMessageRepo(history=[])
    published_text: list[str] = []
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    session.__dict__["_publish_text_delta_event"] = lambda **kwargs: (
        published_text.append(cast(str, kwargs["text"]))
    )

    with pytest.raises(AssistantRunError) as exc_info:
        AgentLlmSession._raise_assistant_run_error(
            session,
            request=_build_request(),
            error_code="network_timeout",
            error_message="Connection to the model endpoint timed out.",
        )

    assert message_repo.pruned_conversation_ids == ["conv-1"]
    assert len(message_repo.append_calls) == 1
    appended = message_repo.append_calls[0][0]
    assert isinstance(appended, ModelResponse)
    assert published_text
    assert "timed out" in published_text[0]
    assert exc_info.value.payload.error_code == "network_timeout"
    assert "timed out" in exc_info.value.payload.assistant_message


def test_raise_terminal_model_api_failure_emits_retry_exhausted_before_terminal_error() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=1)
    retry_exhausted_calls: list[dict[str, object]] = []
    raised_errors: list[dict[str, object]] = []
    session.__dict__["_handle_retry_exhausted"] = lambda **kwargs: (
        retry_exhausted_calls.append(kwargs)
    )
    session.__dict__["_raise_assistant_run_error"] = lambda **kwargs: (
        raised_errors.append(kwargs),
        (_ for _ in ()).throw(RuntimeError("terminal")),
    )

    error = ModelAPIError("gpt-test", "rate limited")

    with pytest.raises(RuntimeError, match="terminal"):
        AgentLlmSession._raise_terminal_model_api_failure(
            session,
            request=_build_request(),
            error=error,
            retry_error=LlmRetryErrorInfo(
                message="slow down",
                status_code=429,
                error_code="rate_limited",
                retryable=True,
                rate_limited=True,
            ),
            retry_number=1,
            total_attempts=2,
            error_message="rate limited",
            fallback_status=_FallbackAttemptStatus.SKIPPED,
        )

    assert len(retry_exhausted_calls) == 1
    assert len(raised_errors) == 1
    assert raised_errors[0]["error_code"] == "rate_limited"


def test_raise_terminal_model_api_failure_skips_retry_exhausted_after_fallback_exhausted() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=1)
    retry_exhausted_calls: list[dict[str, object]] = []
    raised_errors: list[dict[str, object]] = []
    session.__dict__["_handle_retry_exhausted"] = lambda **kwargs: (
        retry_exhausted_calls.append(kwargs)
    )
    session.__dict__["_raise_assistant_run_error"] = lambda **kwargs: (
        raised_errors.append(kwargs),
        (_ for _ in ()).throw(RuntimeError("terminal")),
    )

    error = ModelAPIError("gpt-test", "rate limited")

    with pytest.raises(RuntimeError, match="terminal"):
        AgentLlmSession._raise_terminal_model_api_failure(
            session,
            request=_build_request(),
            error=error,
            retry_error=LlmRetryErrorInfo(
                message="slow down",
                status_code=429,
                error_code="rate_limited",
                retryable=True,
                rate_limited=True,
            ),
            retry_number=1,
            total_attempts=2,
            error_message="rate limited",
            fallback_status=_FallbackAttemptStatus.EXHAUSTED,
        )

    assert retry_exhausted_calls == []
    assert len(raised_errors) == 1
