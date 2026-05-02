# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.multi_model_evaluator import (
    MultiModelSemanticEvaluator,
    _aggregate_results,
)
from relay_teams.agents.tasks.enums import EvaluationAggregation
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
)


def _passing_evaluator(
    request: SemanticEvaluationRequest,
    confidence: float = 0.9,
) -> SemanticEvaluationResult:
    return SemanticEvaluationResult(
        criterion=request.criterion,
        passed=True,
        confidence=confidence,
        evaluator="rule",
    )


def _failing_evaluator(
    request: SemanticEvaluationRequest,
    confidence: float = 0.3,
) -> SemanticEvaluationResult:
    return SemanticEvaluationResult(
        criterion=request.criterion,
        passed=False,
        confidence=confidence,
        evaluator="rule",
    )


def _fake_request(criterion: str = "test criterion") -> SemanticEvaluationRequest:
    return SemanticEvaluationRequest(
        task_id="task-1",
        criterion=criterion,
    )


def test_multi_model_single_evaluator_delegates() -> None:
    evaluator = MultiModelSemanticEvaluator(
        evaluators=(_passing_evaluator,),
    )
    result = evaluator(_fake_request())
    assert result.passed is True
    assert result.evaluator == "rule"


def test_multi_model_majority_passes() -> None:
    evaluator = MultiModelSemanticEvaluator(
        evaluators=(_passing_evaluator, _passing_evaluator, _failing_evaluator),
        aggregation=EvaluationAggregation.MAJORITY,
    )
    result = evaluator(_fake_request())
    assert result.passed is True
    assert "majority" in result.reason


def test_multi_model_majority_fails() -> None:
    evaluator = MultiModelSemanticEvaluator(
        evaluators=(_failing_evaluator, _failing_evaluator, _passing_evaluator),
        aggregation=EvaluationAggregation.MAJORITY,
    )
    result = evaluator(_fake_request())
    assert result.passed is False


def test_multi_model_unanimous_passes() -> None:
    evaluator = MultiModelSemanticEvaluator(
        evaluators=(_passing_evaluator, _passing_evaluator),
        aggregation=EvaluationAggregation.UNANIMOUS,
    )
    result = evaluator(_fake_request())
    assert result.passed is True


def test_multi_model_unanimous_fails_on_single_failure() -> None:
    evaluator = MultiModelSemanticEvaluator(
        evaluators=(_passing_evaluator, _failing_evaluator),
        aggregation=EvaluationAggregation.UNANIMOUS,
    )
    result = evaluator(_fake_request())
    assert result.passed is False


def test_multi_model_weighted_passes() -> None:
    def high_conf_pass(req: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        return _passing_evaluator(req, confidence=0.9)

    def low_conf_fail(req: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        return _failing_evaluator(req, confidence=0.3)

    evaluator = MultiModelSemanticEvaluator(
        evaluators=(high_conf_pass, low_conf_fail),
        aggregation=EvaluationAggregation.WEIGHTED,
    )
    result = evaluator(_fake_request())
    assert result.passed is True


def test_multi_model_weights_below_minimum_agreement_warns() -> None:
    evaluator = MultiModelSemanticEvaluator(
        evaluators=(_passing_evaluator, _failing_evaluator),
        aggregation=EvaluationAggregation.MAJORITY,
        minimum_agreement=0.8,
    )
    result = evaluator(_fake_request())
    assert "WARNING" in result.reason


def test_multi_model_handles_evaluator_exception() -> None:
    def failing_evaluator(
        _request: SemanticEvaluationRequest,
    ) -> SemanticEvaluationResult:
        raise RuntimeError("model error")

    evaluator = MultiModelSemanticEvaluator(
        evaluators=(
            failing_evaluator,
            _passing_evaluator,
            _passing_evaluator,
        ),
        aggregation=EvaluationAggregation.MAJORITY,
    )
    result = evaluator(_fake_request())
    assert result.passed is True


def test_multi_model_raises_on_empty_evaluators() -> None:
    with pytest.raises(ValueError, match="At least one evaluator"):
        MultiModelSemanticEvaluator(evaluators=())


def test_aggregate_results_merges_evidence_ids() -> None:
    results = [
        SemanticEvaluationResult(
            criterion="x",
            passed=True,
            confidence=0.9,
            evaluator="a",
            evidence_ids=("ev1", "ev2"),
        ),
        SemanticEvaluationResult(
            criterion="x",
            passed=True,
            confidence=0.8,
            evaluator="b",
            evidence_ids=("ev2", "ev3"),
        ),
    ]
    aggregated = _aggregate_results(
        results=results,
        aggregation=EvaluationAggregation.MAJORITY,
        minimum_agreement=0.6,
    )
    assert aggregated.evidence_ids == ("ev1", "ev2", "ev3")
