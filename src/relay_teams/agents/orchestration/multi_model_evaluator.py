# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Callable

from relay_teams.agents.tasks.enums import EvaluationAggregation
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
)
from relay_teams.logger import get_logger, log_event

_LOGGER = get_logger(__name__)

SemanticVerificationEvaluator = Callable[
    [SemanticEvaluationRequest], SemanticEvaluationResult
]


class MultiModelSemanticEvaluator:
    """Aggregates results from multiple semantic evaluators.

    Supports three aggregation strategies:

    * **MAJORITY**: passes when more than half of evaluators pass.
    * **UNANIMOUS**: passes only when all evaluators pass.
    * **WEIGHTED**: weighted vote by each evaluator's confidence score.

    If the agreement ratio is below ``minimum_agreement``, the result
    includes a warning in the reason field.
    """

    def __init__(
        self,
        *,
        evaluators: tuple[SemanticVerificationEvaluator, ...],
        aggregation: EvaluationAggregation = EvaluationAggregation.MAJORITY,
        minimum_agreement: float = 0.6,
    ) -> None:
        if not evaluators:
            raise ValueError("At least one evaluator is required")
        self._evaluators = evaluators
        self._aggregation = aggregation
        self._minimum_agreement = minimum_agreement

    def __call__(self, request: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        if len(self._evaluators) == 1:
            return self._evaluators[0](request)

        individual_results: list[SemanticEvaluationResult] = []
        for evaluator in self._evaluators:
            try:
                result = evaluator(request)
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    event="verification.multi_model_evaluator_failed",
                    message="One evaluator in multi-model evaluation failed",
                    payload={
                        "criterion": request.criterion,
                        "error": str(exc),
                    },
                )
                result = SemanticEvaluationResult(
                    criterion=request.criterion,
                    passed=False,
                    confidence=0.0,
                    reason=f"Evaluator failed: {exc}",
                    evaluator="multi_model_failed",
                )
            individual_results.append(result)

        return _aggregate_results(
            results=individual_results,
            aggregation=self._aggregation,
            minimum_agreement=self._minimum_agreement,
        )


def _aggregate_results(
    *,
    results: list[SemanticEvaluationResult],
    aggregation: EvaluationAggregation,
    minimum_agreement: float,
) -> SemanticEvaluationResult:
    total = len(results)
    pass_count = sum(1 for result in results if result.passed)
    agreement_ratio = pass_count / total if total > 0 else 0.0

    if aggregation == EvaluationAggregation.MAJORITY:
        passed = pass_count > total / 2
    elif aggregation == EvaluationAggregation.UNANIMOUS:
        passed = pass_count == total
    elif aggregation == EvaluationAggregation.WEIGHTED:
        total_weight = sum(result.confidence for result in results)
        pass_weight = sum(result.confidence for result in results if result.passed)
        passed = pass_weight > total_weight / 2 if total_weight > 0 else False
    else:
        passed = pass_count > total / 2

    all_evidence_ids: list[str] = []
    for result in results:
        all_evidence_ids.extend(result.evidence_ids)
    evidence_ids = tuple(dict.fromkeys(all_evidence_ids))

    confidence = (
        sum(result.confidence for result in results) / total if total > 0 else 0.0
    )

    _VERDICT_LABELS = {True: "PASS", False: "FAIL"}
    verdict_label = _VERDICT_LABELS.get(passed, "FAIL")
    individual_summaries = "; ".join(
        f"[{result.evaluator}] {verdict_label} ({result.confidence:.2f})"
        for result, verdict_label in zip(
            results,
            (_VERDICT_LABELS[r.passed] for r in results),
        )
    )

    reason_parts: list[str] = [
        f"{aggregation.value} aggregation: {verdict_label} "
        f"({pass_count}/{total} pass, ratio={agreement_ratio:.2f}).",
        individual_summaries,
    ]
    if agreement_ratio < minimum_agreement:
        reason_parts.append(
            f"WARNING: agreement ratio {agreement_ratio:.2f} "
            f"below minimum {minimum_agreement:.2f}."
        )

    return SemanticEvaluationResult(
        criterion=results[0].criterion if results else "",
        passed=passed,
        confidence=confidence,
        reason=" ".join(reason_parts),
        evidence_ids=evidence_ids,
        evaluator="multi_model",
    )
