# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock


from relay_teams.agents.execution.failure_reporting import FailureHandlingService
from relay_teams.providers.provider_contracts import LLMRequest


def _make_request() -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        instance_id="inst-1",
        role_id="writer",
        system_prompt="sys",
        user_prompt=None,
    )


class _ServiceFixture:
    def __init__(self) -> None:
        self.hub = MagicMock()
        config = MagicMock()
        provider = MagicMock()
        provider.value = "test-provider"
        config.provider = provider
        config.model = "test-model"
        self.service = FailureHandlingService(
            config=config,
            profile_name="test-profile",
            retry_config=MagicMock(),
            message_repo=MagicMock(),
            run_event_hub=self.hub,
        )


class TestHandleFallbackActivated:
    def test_publishes_event(self) -> None:
        fix = _ServiceFixture()
        decision = MagicMock()
        decision.policy_id = "policy-1"
        decision.from_profile_name = "prof-a"
        decision.to_profile_name = "prof-b"
        from_provider = MagicMock()
        from_provider.value = "prov-a"
        to_provider = MagicMock()
        to_provider.value = "prov-b"
        decision.from_provider = from_provider
        decision.to_provider = to_provider
        decision.from_model = "mod-a"
        decision.to_model = "mod-b"
        decision.hop = 1
        decision.reason = "rate_limit"
        fix.service.handle_fallback_activated(
            request=_make_request(),
            retry_number=1,
            total_attempts=3,
            decision=decision,
        )
        fix.hub.publish.assert_called_once()


class TestHandleFallbackExhausted:
    def test_publishes_event(self) -> None:
        from relay_teams.providers.llm_retry import LlmRetryErrorInfo

        fix = _ServiceFixture()
        error = LlmRetryErrorInfo(
            message="rate limited",
            status_code=429,
            error_code="rate_limit",
            retryable=True,
            rate_limited=True,
        )
        fallback_state = MagicMock()
        fallback_state.hop = 1
        fallback_state.visited_profiles = ["prof-a"]
        fix.service.handle_fallback_exhausted(
            request=_make_request(),
            retry_number=2,
            total_attempts=3,
            error=error,
            fallback_state=fallback_state,
        )
        fix.hub.publish.assert_called_once()
