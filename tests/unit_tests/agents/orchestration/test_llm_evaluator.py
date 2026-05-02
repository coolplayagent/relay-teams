# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.orchestration.llm_evaluator import (
    LLMEvaluator,
    _build_spec_quality_prompt,
    _parse_llm_response,
    _fallback_evaluation_result,
)
from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationRequest,
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.agents.tasks.models import SemanticEvaluationRequest


@pytest.fixture
def mock_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value="")
    return provider


@pytest.fixture
def evaluator(mock_provider: AsyncMock) -> LLMEvaluator:
    return LLMEvaluator(provider=mock_provider, model="gpt-4o")


def _valid_llm_json_response() -> str:
    return json.dumps(
        {
            "scores": [
                {"dimension": "completeness", "score": 4, "reasoning": "Good"},
                {"dimension": "clarity", "score": 5, "reasoning": "Clear"},
                {"dimension": "testability", "score": 3, "reasoning": "Needs work"},
                {"dimension": "consistency", "score": 4, "reasoning": "Consistent"},
                {"dimension": "appropriateness", "score": 4, "reasoning": "Appropriate"},
            ],
            "summary": "Overall good spec with minor testability gaps.",
            "recommendations": [
                "Add more verification commands",
                "Clarify edge cases",
            ],
        }
    )


class TestSpecQualityScoring:
    @pytest.mark.asyncio
    async def test_evaluate_spec_quality_returns_scores(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.return_value = _valid_llm_json_response()
        request = LLMEvaluationRequest(
            task_id="task-1",
            spec_summary="Build the endpoint",
            requirements=("return HTTP 201",),
            constraints=("do not change the public route",),
            acceptance_criteria=("new API test passes",),
        )
        result = await evaluator.evaluate_spec_quality(request)

        assert isinstance(result, LLMEvaluationResult)
        assert len(result.scores) == 5
        assert all(isinstance(s, LLMEvaluationScore) for s in result.scores)
        assert result.overall_score == 4.0
        assert "minor testability gaps" in result.summary
        assert len(result.recommendations) == 2
        assert result.fallback is False
        assert result.evaluator == "llm"

    @pytest.mark.asyncio
    async def test_evaluate_spec_quality_calls_provider(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.return_value = _valid_llm_json_response()
        request = LLMEvaluationRequest(
            task_id="task-1",
            spec_summary="Test",
            requirements=("req-1",),
        )
        await evaluator.evaluate_spec_quality(request)

        mock_provider.generate.assert_awaited_once()
        llm_request = mock_provider.generate.call_args[0][0]
        assert "Evaluate the following specification" in llm_request.user_prompt
        assert "Test" in llm_request.user_prompt
        assert "req-1" in llm_request.user_prompt


class TestAcceptanceCriteriaEvaluation:
    @pytest.mark.asyncio
    async def test_evaluate_acceptance_criteria(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.return_value = json.dumps(
            {
                "scores": [
                    {
                        "dimension": "criterion_satisfied",
                        "score": 5,
                        "reasoning": "All tests pass",
                    },
                ],
                "summary": "Criteria fully met.",
                "recommendations": [],
            }
        )
        request = LLMEvaluationRequest(
            task_id="task-1",
            acceptance_criteria=("new API test passes",),
            task_result="All 3 API tests passed successfully.",
        )
        result = await evaluator.evaluate_acceptance_criteria(request)

        assert isinstance(result, LLMEvaluationResult)
        assert len(result.scores) == 1
        assert result.scores[0].score == 5
        assert "fully met" in result.summary
        assert result.fallback is False


class TestFallbackBehavior:
    @pytest.mark.asyncio
    async def test_fallback_on_provider_failure(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.side_effect = RuntimeError("provider down")
        request = LLMEvaluationRequest(
            task_id="task-1",
            spec_summary="Test",
        )
        result = await evaluator.evaluate_spec_quality(request)

        assert result.fallback is True
        assert result.evaluator == "rule"
        assert len(result.scores) == 5
        assert all(s.score == 3 for s in result.scores)
        assert "fallback" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_fallback_on_parse_failure(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.return_value = "not valid json {{{"
        request = LLMEvaluationRequest(task_id="task-1")
        result = await evaluator.evaluate_spec_quality(request)

        assert result.fallback is True
        assert result.evaluator == "rule"


class TestResponseParsing:
    def test_parse_valid_json(self) -> None:
        response = _valid_llm_json_response()
        result = _parse_llm_response(response)

        assert len(result.scores) == 5
        assert result.overall_score == 4.0
        assert result.summary == "Overall good spec with minor testability gaps."

    def test_parse_json_with_markdown_fences(self) -> None:
        raw = _valid_llm_json_response()
        fenced = f"```json\n{raw}\n```"
        result = _parse_llm_response(fenced)

        assert len(result.scores) == 5
        assert result.overall_score == 4.0

    def test_parse_empty_scores_returns_fallback(self) -> None:
        response = json.dumps({"scores": [], "summary": "empty"})
        result = _parse_llm_response(response)

        assert result.fallback is True

    def test_parse_invalid_json_returns_fallback(self) -> None:
        result = _parse_llm_response("not json at all")
        assert result.fallback is True

    def test_parse_missing_score_defaults_to_3(self) -> None:
        response = json.dumps(
            {
                "scores": [{"dimension": "test"}],
                "summary": "partial",
            }
        )
        result = _parse_llm_response(response)

        assert len(result.scores) == 1
        assert result.scores[0].score == 3

    def test_overall_score_is_average(self) -> None:
        response = json.dumps(
            {
                "scores": [
                    {"dimension": "a", "score": 2},
                    {"dimension": "b", "score": 4},
                ],
                "summary": "mixed",
            }
        )
        result = _parse_llm_response(response)

        assert result.overall_score == 3.0


class TestFallbackEvaluationResult:
    def test_fallback_result_structure(self) -> None:
        result = _fallback_evaluation_result()

        assert result.fallback is True
        assert result.evaluator == "rule"
        assert len(result.scores) == 5
        dimensions = [s.dimension for s in result.scores]
        assert "completeness" in dimensions
        assert "clarity" in dimensions
        assert "testability" in dimensions
        assert "consistency" in dimensions
        assert "appropriateness" in dimensions
        assert all(s.score == 3 for s in result.scores)
        assert result.overall_score == 3.0
        assert len(result.recommendations) > 0


class TestModelValidation:
    def test_score_clamped_to_range(self) -> None:
        score_low = LLMEvaluationScore(dimension="test", score=-1, reasoning="")
        assert score_low.score == 1

        score_high = LLMEvaluationScore(dimension="test", score=10, reasoning="")
        assert score_high.score == 5

    def test_score_within_range(self) -> None:
        score = LLMEvaluationScore(dimension="test", score=3, reasoning="ok")
        assert score.score == 3

    def test_evaluation_result_overall_score_bounds(self) -> None:
        result = LLMEvaluationResult(
            scores=[LLMEvaluationScore(dimension="test", score=5)],
            overall_score=5.0,
            summary="max",
        )
        assert result.overall_score == 5.0

    def test_request_model_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            LLMEvaluationRequest(
                task_id="task-1",
                unknown_field="value",  # type: ignore[call-arg]
            )


class TestPromptBuilding:
    def test_spec_quality_prompt_includes_all_fields(self) -> None:
        request = LLMEvaluationRequest(
            task_id="task-1",
            spec_summary="Build the endpoint",
            requirements=("req-1", "req-2"),
            constraints=("const-1",),
            acceptance_criteria=("ac-1",),
            evidence_expectations=("ee-1",),
        )
        prompt = _build_spec_quality_prompt(request)

        assert "Build the endpoint" in prompt
        assert "req-1" in prompt
        assert "req-2" in prompt
        assert "const-1" in prompt
        assert "ac-1" in prompt
        assert "ee-1" in prompt
        assert "completeness" in prompt
        assert "clarity" in prompt
        assert "testability" in prompt

    def test_spec_quality_prompt_omits_empty_fields(self) -> None:
        request = LLMEvaluationRequest(task_id="task-1")
        prompt = _build_spec_quality_prompt(request)

        assert "Summary:" not in prompt
        assert "Requirements:" not in prompt
        assert "Constraints:" not in prompt


class TestSemanticEvaluatorBridge:
    @pytest.mark.asyncio
    async def test_as_semantic_evaluator_success(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.return_value = json.dumps(
            {
                "scores": [
                    {
                        "dimension": "criterion_met",
                        "score": 4,
                        "reasoning": "Mostly satisfied",
                    },
                ],
                "summary": "Criteria mostly met.",
                "recommendations": [],
            }
        )
        bridge = evaluator.as_semantic_evaluator()
        semantic_request = SemanticEvaluationRequest(
            task_id="task-1",
            criterion="new API test passes",
            result_excerpt="All tests passed.",
        )
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: bridge(semantic_request)
        )

        assert result.criterion == "new API test passes"
        assert result.passed is True
        assert result.evaluator == "llm"
        assert result.confidence > 0.0

    @pytest.mark.asyncio
    async def test_as_semantic_evaluator_failure_fallback(
        self,
        evaluator: LLMEvaluator,
        mock_provider: AsyncMock,
    ) -> None:
        mock_provider.generate.side_effect = RuntimeError("down")
        bridge = evaluator.as_semantic_evaluator()
        semantic_request = SemanticEvaluationRequest(
            task_id="task-1",
            criterion="test",
            result_excerpt="result",
        )
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: bridge(semantic_request)
        )

        assert result.passed is False
        assert result.confidence == 0.0
        assert result.evaluator == "rule"
