# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.orchestration.llm_evaluator import (
    LLMEvaluator,
    _build_acceptance_prompt,
    _build_spec_quality_prompt,
    _fallback_evaluation_result,
    _fallback_semantic_result,
    _parse_llm_response,
)
from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationRequest,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
)
from relay_teams.providers.provider_contracts import LLMProvider


def _make_provider(response: str = '{"scores": []}') -> LLMProvider:
    provider = MagicMock(spec=LLMProvider)
    provider.generate = AsyncMock(return_value=response)
    return provider


def _make_evaluator(provider: LLMProvider | None = None) -> LLMEvaluator:
    return LLMEvaluator(
        provider=provider or _make_provider(),
        model="test-model",
        run_id="r1",
        trace_id="t1",
        task_id="task1",
        session_id="s1",
        workspace_id="w1",
        instance_id="i1",
        role_id="role1",
    )


def test_fallback_semantic_result() -> None:
    request = SemanticEvaluationRequest(
        task_id="t1", criterion="test", result_excerpt="output"
    )
    result = _fallback_semantic_result(request)
    assert result.passed is False
    assert result.evaluator == "rule"
    assert result.criterion == "test"


def test_fallback_evaluation_result() -> None:
    result = _fallback_evaluation_result()
    assert result.fallback is True
    assert result.overall_score == 3.0
    assert len(result.scores) == 5
    assert result.evaluator == "rule"


@pytest.mark.asyncio
async def test_evaluate_spec_quality() -> None:
    llm_response = json.dumps(
        {
            "scores": [
                {"dimension": "completeness", "score": 4, "reasoning": "good"},
                {"dimension": "clarity", "score": 5, "reasoning": "clear"},
            ],
            "summary": "Solid spec",
            "recommendations": ["Add more examples"],
        }
    )
    provider = _make_provider(llm_response)
    evaluator = _make_evaluator(provider)
    request = LLMEvaluationRequest(
        task_id="t1",
        spec_summary="A test spec",
        requirements=("req1",),
        acceptance_criteria=("ac1",),
    )
    result = await evaluator.evaluate_spec_quality(request)
    assert result.overall_score == 4.5
    assert result.summary == "Solid spec"
    assert len(result.scores) == 2
    assert result.fallback is False


@pytest.mark.asyncio
async def test_evaluate_acceptance_criteria() -> None:
    llm_response = json.dumps(
        {
            "scores": [
                {"dimension": "acceptance", "score": 4, "reasoning": "met"},
            ],
            "summary": "Criteria met",
            "recommendations": [],
        }
    )
    provider = _make_provider(llm_response)
    evaluator = _make_evaluator(provider)
    request = LLMEvaluationRequest(
        task_id="t1",
        acceptance_criteria=("all tests pass",),
        task_result="5/5 tests passed",
    )
    result = await evaluator.evaluate_acceptance_criteria(request)
    assert result.overall_score == 4.0
    assert result.summary == "Criteria met"


@pytest.mark.asyncio
async def test_run_evaluation_fallback_on_provider_error() -> None:
    provider = _make_provider()
    provider.generate = AsyncMock(side_effect=RuntimeError("API error"))
    evaluator = _make_evaluator(provider)
    request = LLMEvaluationRequest(task_id="t1")
    result = await evaluator.evaluate_spec_quality(request)
    assert result.fallback is True


def test_parse_llm_response_with_markdown_fences() -> None:
    raw = '```json\n{"scores": [{"dimension": "clarity", "score": 4, "reasoning": "ok"}], "summary": "fine", "recommendations": []}\n```'
    result = _parse_llm_response(raw)
    assert result.overall_score == 4.0
    assert result.fallback is False


def test_parse_llm_response_invalid_json_returns_fallback() -> None:
    result = _parse_llm_response("not json at all")
    assert result.fallback is True


def test_parse_llm_response_empty_scores_returns_fallback() -> None:
    result = _parse_llm_response(
        json.dumps({"scores": [], "summary": "", "recommendations": []})
    )
    assert result.fallback is True


def test_as_semantic_evaluator_success() -> None:
    llm_response = json.dumps(
        {
            "scores": [
                {"dimension": "acceptance", "score": 4, "reasoning": "met"},
            ],
            "summary": "Passed",
            "recommendations": [],
        }
    )
    provider = _make_provider(llm_response)
    evaluator = _make_evaluator(provider)
    semantic_fn = evaluator.as_semantic_evaluator()
    request = SemanticEvaluationRequest(
        task_id="t1", criterion="all tests pass", result_excerpt="5/5 passed"
    )
    result = semantic_fn(request)
    assert result.passed is True
    assert result.evaluator == "llm"
    assert result.criterion == "all tests pass"


def test_as_semantic_evaluator_fallback_on_exception() -> None:
    provider = _make_provider()
    provider.generate = AsyncMock(side_effect=RuntimeError("fail"))
    evaluator = _make_evaluator(provider)
    semantic_fn = evaluator.as_semantic_evaluator()
    request = SemanticEvaluationRequest(task_id="t1", criterion="c", result_excerpt="r")
    result = semantic_fn(request)
    assert result.passed is False
    assert result.evaluator == "rule"


def test_as_semantic_evaluator_fallback_on_fallback_result() -> None:
    provider = _make_provider()
    provider.generate = AsyncMock(return_value="not valid json{{{")
    evaluator = _make_evaluator(provider)
    semantic_fn = evaluator.as_semantic_evaluator()
    request = SemanticEvaluationRequest(task_id="t1", criterion="c", result_excerpt="r")
    result = semantic_fn(request)
    assert result.passed is False
    assert result.evaluator == "rule"


def test_build_spec_quality_prompt_includes_fields() -> None:
    request = LLMEvaluationRequest(
        task_id="t1",
        spec_summary="Test summary",
        requirements=("req1", "req2"),
        constraints=("c1",),
        acceptance_criteria=("ac1",),
        evidence_expectations=("ev1",),
    )
    prompt = _build_spec_quality_prompt(request)
    assert "Test summary" in prompt
    assert "req1" in prompt
    assert "c1" in prompt
    assert "ac1" in prompt
    assert "ev1" in prompt
    assert "completeness" in prompt


def test_build_acceptance_prompt_includes_fields() -> None:
    request = LLMEvaluationRequest(
        task_id="t1",
        acceptance_criteria=("tests pass",),
        task_result="All passed",
    )
    prompt = _build_acceptance_prompt(request)
    assert "tests pass" in prompt
    assert "All passed" in prompt
