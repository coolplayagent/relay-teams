# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.execution.spec_drift_evaluator import evaluate_spec_drift
from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.agents.tasks.models import TaskSpec


def _make_spec() -> TaskSpec:
    return TaskSpec(
        summary="Test spec",
        requirements=("r1",),
        constraints=("c1",),
        acceptance_criteria=("a1",),
    )


def _make_evaluator(
    overall_score: float = 4.5,
    fallback: bool = False,
) -> MagicMock:
    int_score = int(overall_score)
    scores = (
        LLMEvaluationScore(
            dimension="requirements_coverage",
            score=int_score,
            reasoning="Good coverage",
        ),
        LLMEvaluationScore(
            dimension="constraint_fidelity",
            score=int_score,
            reasoning="Well aligned",
        ),
    )
    result = LLMEvaluationResult(
        overall_score=overall_score,
        scores=list(scores),
        summary="Looks good",
        evaluator="test-evaluator",
        fallback=fallback,
    )
    evaluator = MagicMock()
    evaluator.evaluate_spec_quality = AsyncMock(return_value=result)
    return evaluator


@pytest.mark.asyncio
class TestEvaluateSpecDrift:
    async def test_returns_evaluation_with_no_drift(self) -> None:
        evaluator = _make_evaluator(overall_score=4.5)
        spec = _make_spec()
        result = await evaluate_spec_drift(
            spec=spec,
            task_id="task-1",
            artifact_id="art-1",
            session_id="sess-1",
            trace_id="trace-1",
            checkpoint_seq=1,
            evaluator=evaluator,
            drift_score_threshold=3.0,
        )
        assert result.drift_detected is False
        assert result.overall_score == 4.5
        assert result.evaluator == "test-evaluator"
        assert result.checkpoint_seq == 1
        assert result.task_id == "task-1"
        assert result.artifact_id == "art-1"
        assert result.fallback is False

    async def test_detects_drift_below_threshold(self) -> None:
        evaluator = _make_evaluator(overall_score=2.0)
        spec = _make_spec()
        result = await evaluate_spec_drift(
            spec=spec,
            task_id="task-1",
            artifact_id="art-1",
            session_id="sess-1",
            trace_id="trace-1",
            checkpoint_seq=1,
            evaluator=evaluator,
            drift_score_threshold=3.0,
        )
        assert result.drift_detected is True
        assert result.overall_score == 2.0

    async def test_handles_evaluator_exception_with_fallback(self) -> None:
        evaluator = MagicMock()
        evaluator.evaluate_spec_quality = AsyncMock(
            side_effect=RuntimeError("LLM unavailable")
        )
        spec = _make_spec()
        result = await evaluate_spec_drift(
            spec=spec,
            task_id="task-1",
            artifact_id="art-1",
            session_id="sess-1",
            trace_id="trace-1",
            checkpoint_seq=2,
            evaluator=evaluator,
            drift_score_threshold=3.0,
        )
        assert result.fallback is True
        assert result.overall_score == 3.0
        assert result.drift_detected is False

    async def test_marks_fallback_when_result_is_fallback(self) -> None:
        evaluator = _make_evaluator(overall_score=4.0, fallback=True)
        spec = _make_spec()
        result = await evaluate_spec_drift(
            spec=spec,
            task_id="task-1",
            artifact_id="art-1",
            session_id="sess-1",
            trace_id="trace-1",
            checkpoint_seq=1,
            evaluator=evaluator,
        )
        assert result.fallback is True

    async def test_generates_evaluation_id(self) -> None:
        evaluator = _make_evaluator()
        spec = _make_spec()
        result = await evaluate_spec_drift(
            spec=spec,
            task_id="task-1",
            artifact_id="art-1",
            session_id="sess-1",
            trace_id="trace-1",
            checkpoint_seq=1,
            evaluator=evaluator,
        )
        assert result.evaluation_id.startswith("speval-")

    async def test_scores_json_populated(self) -> None:
        evaluator = _make_evaluator(overall_score=4.0)
        spec = _make_spec()
        result = await evaluate_spec_drift(
            spec=spec,
            task_id="task-1",
            artifact_id="art-1",
            session_id="sess-1",
            trace_id="trace-1",
            checkpoint_seq=1,
            evaluator=evaluator,
        )
        import json

        scores = json.loads(result.scores_json)
        assert len(scores) == 2
        assert scores[0]["dimension"] == "requirements_coverage"
