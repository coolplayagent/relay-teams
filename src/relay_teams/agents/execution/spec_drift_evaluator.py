# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from uuid import uuid4

from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator
from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationRequest,
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.agents.tasks.models import (
    SpecCheckpointEvaluation,
    TaskSpec,
)
from relay_teams.logger import get_logger, log_event

_LOGGER = get_logger(__name__)


async def evaluate_spec_drift(
    *,
    spec: TaskSpec,
    task_id: str,
    artifact_id: str,
    session_id: str,
    trace_id: str,
    checkpoint_seq: int,
    evaluator: LLMEvaluator,
    drift_score_threshold: float = 3.0,
) -> SpecCheckpointEvaluation:
    evaluation_id = f"speval-{uuid4().hex[:12]}"
    eval_request = LLMEvaluationRequest(
        task_id=task_id,
        spec_summary=spec.summary,
        requirements=spec.requirements,
        constraints=spec.constraints,
        acceptance_criteria=spec.acceptance_criteria,
        evidence_expectations=spec.evidence_expectations,
    )

    fallback = False
    result: LLMEvaluationResult
    try:
        result = await evaluator.evaluate_spec_quality(eval_request)
        if result.fallback:
            fallback = True
    except Exception as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            event="spec_checkpoint.drift_evaluation_failed",
            message="Drift evaluation failed, using fallback",
            payload={"error": str(exc), "task_id": task_id},
        )
        result = LLMEvaluationResult(
            scores=[
                LLMEvaluationScore(
                    dimension="completeness",
                    score=3,
                    reasoning="LLM evaluation unavailable; using neutral fallback.",
                ),
            ],
            overall_score=3.0,
            summary="LLM evaluation failed; fallback to rule-based assessment.",
            recommendations=[
                "Manual review recommended due to LLM evaluation failure.",
            ],
        )
        fallback = True

    drift_detected = result.overall_score < drift_score_threshold
    if drift_detected:
        log_event(
            _LOGGER,
            logging.WARNING,
            event="spec_checkpoint.drift_detected",
            message="Spec checkpoint drift detected",
            payload={
                "task_id": task_id,
                "checkpoint_seq": checkpoint_seq,
                "overall_score": result.overall_score,
                "threshold": drift_score_threshold,
            },
        )

    scores_json = json.dumps(
        [
            {
                "dimension": score.dimension,
                "score": score.score,
                "reasoning": score.reasoning,
            }
            for score in result.scores
        ]
    )

    drift_dimensions = [
        score.dimension
        for score in result.scores
        if score.score < drift_score_threshold
    ]
    drift_detail = json.dumps({"flagged_dimensions": drift_dimensions})

    return SpecCheckpointEvaluation(
        evaluation_id=evaluation_id,
        task_id=task_id,
        artifact_id=artifact_id,
        session_id=session_id,
        trace_id=trace_id,
        checkpoint_seq=checkpoint_seq,
        evaluator=result.evaluator,
        fallback=fallback,
        overall_score=result.overall_score,
        scores_json=scores_json,
        summary=result.summary,
        drift_detected=drift_detected,
        drift_detail=drift_detail,
    )
