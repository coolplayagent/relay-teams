# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.verification import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
    VerificationEvaluatorFactory,
    _LlmSemanticEvaluator,
    _deferred_llm_evaluator,
    _parse_llm_evaluator_response,
)
from relay_teams.providers.provider_contracts import LLMProvider


def _make_request(
    criterion: str = "Output must be correct",
    excerpt: str = "Some output",
) -> SemanticEvaluationRequest:
    return SemanticEvaluationRequest(
        task_id="test-task",
        criterion=criterion,
        result_excerpt=excerpt,
    )


class _EchoProvider(LLMProvider):
    """Test provider that returns a canned JSON evaluation."""

    def __init__(
        self, response: str = '{"passed": true, "confidence": 0.8, "reason": "OK"}'
    ) -> None:
        self._response = response

    async def generate(self, _request: object) -> str:
        return self._response


class _FailingProvider(LLMProvider):
    """Test provider that always raises."""

    async def generate(self, _request: object) -> str:
        raise RuntimeError("Provider unavailable")


class TestParseLlmEvaluatorResponse:
    def test_parse_valid_json_response(self) -> None:
        request = _make_request(
            criterion="Output must contain summary",
            excerpt="This is a summary of the task output.",
        )
        result = _parse_llm_evaluator_response(
            request=request,
            response_text='{"passed": true, "confidence": 0.9, "reason": "Contains summary"}',
        )
        assert isinstance(result, SemanticEvaluationResult)
        assert result.passed is True
        assert result.confidence == 0.9
        assert result.reason == "Contains summary"
        assert result.evaluator == "llm_semantic"

    def test_parse_json_embedded_in_text(self) -> None:
        request = _make_request()
        result = _parse_llm_evaluator_response(
            request=request,
            response_text='Here is my evaluation: {"passed": false, "confidence": 0.7, "reason": "Missing details"} end',
        )
        assert result.passed is False
        assert result.confidence == 0.7
        assert result.evaluator == "llm_semantic"

    def test_parse_invalid_json_returns_fallback(self) -> None:
        request = _make_request()
        result = _parse_llm_evaluator_response(
            request=request,
            response_text="This is not valid JSON at all",
        )
        assert result.passed is True
        assert result.confidence == 0.3
        assert result.evaluator == "llm_parse_fallback"


class TestDeferredEvaluator:
    def test_returns_tentative_pass(self) -> None:
        request = _make_request(criterion="Something")
        result = _deferred_llm_evaluator(request)
        assert result.passed is True
        assert result.confidence == 0.3
        assert result.evaluator == "deferred_llm"


class TestFactoryWithRealEvaluator:
    def test_factory_builds_real_evaluator(self) -> None:
        factory = VerificationEvaluatorFactory(llm_provider=_EchoProvider())
        evaluator = factory.build()
        assert evaluator is not None
        assert isinstance(evaluator, _LlmSemanticEvaluator)

    def test_factory_base_evaluator_takes_precedence(self) -> None:
        calls: list[int] = []

        def base_eval(request: SemanticEvaluationRequest) -> SemanticEvaluationResult:
            calls.append(1)
            return SemanticEvaluationResult(
                criterion=request.criterion,
                passed=True,
                confidence=1.0,
                reason="base",
                evaluator="test_base",
            )

        factory = VerificationEvaluatorFactory(
            base_evaluator=base_eval,
            llm_provider=_EchoProvider(),
        )
        result = factory.build()
        assert result is base_eval
