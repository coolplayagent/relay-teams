# -*- coding: utf-8 -*-
"""Coverage gap tests for verification.py LLM evaluator and parse function."""

from __future__ import annotations

from unittest.mock import AsyncMock


from relay_teams.agents.orchestration.verification import (
    _LlmSemanticEvaluator,
    _parse_llm_evaluator_response,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
)


def _make_request(**overrides: object) -> SemanticEvaluationRequest:
    base: dict[str, object] = dict(
        task_id="task_ver",
        criterion="Output must be non-empty",
        result_excerpt="Some excerpt text",
        evidence=[],
    )
    base.update(overrides)
    return SemanticEvaluationRequest(**base)  # type: ignore[arg-type]


class TestLLMSemanticEvaluatorCall:
    """Cover lines 116-117,121,126,129-130,137."""

    def test_call_success(self) -> None:
        provider = AsyncMock()
        provider.generate = AsyncMock(
            return_value='{"passed": true, "confidence": 0.9, "reason": "looks good"}'
        )
        evaluator = _LlmSemanticEvaluator(provider=provider)
        request = _make_request()
        result = evaluator(request)
        assert result.passed is True
        assert result.confidence == 0.9
        assert result.evaluator == "llm_semantic"

    def test_call_exception_fallback(self) -> None:
        provider = AsyncMock()
        provider.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        evaluator = _LlmSemanticEvaluator(provider=provider)
        request = _make_request()
        result = evaluator(request)
        assert result.passed is True
        assert result.confidence == 0.3
        assert result.evaluator == "llm_fallback"

    def test_call_returns_none_response(self) -> None:
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value=None)
        evaluator = _LlmSemanticEvaluator(provider=provider)
        request = _make_request()
        result = evaluator(request)
        # Should handle None response gracefully
        assert isinstance(result, SemanticEvaluationResult)


class TestParseLLMEvaluatorResponse:
    """Cover lines 166-167 (JSON parse fallback)."""

    def test_parse_valid_json(self) -> None:
        request = _make_request()
        result = _parse_llm_evaluator_response(
            request=request,
            response_text='Some text {"passed": false, "confidence": 0.7, "reason": "incomplete"} trailing',
        )
        assert result.passed is False
        assert result.confidence == 0.7
        assert result.evaluator == "llm_semantic"

    def test_parse_invalid_json_fallback(self) -> None:
        request = _make_request()
        result = _parse_llm_evaluator_response(
            request=request,
            response_text="no json here at all",
        )
        assert result.passed is True
        assert result.evaluator == "llm_parse_fallback"

    def test_parse_malformed_json_braces(self) -> None:
        request = _make_request()
        result = _parse_llm_evaluator_response(
            request=request,
            response_text="{not valid json}",
        )
        assert isinstance(result, SemanticEvaluationResult)

    def test_parse_empty_string(self) -> None:
        request = _make_request()
        result = _parse_llm_evaluator_response(
            request=request,
            response_text="",
        )
        assert result.passed is True
