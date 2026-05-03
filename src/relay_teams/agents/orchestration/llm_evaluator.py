# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationRequest,
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.sessions.runs.run_models import RunKind

_LOGGER = get_logger(__name__)

SemanticVerificationEvaluator = Callable[
    [SemanticEvaluationRequest], SemanticEvaluationResult
]

_SPEC_QUALITY_DIMENSIONS = (
    "completeness",
    "clarity",
    "testability",
    "consistency",
    "appropriateness",
)


class LLMEvaluator:
    """LLM-powered evaluator for spec quality and acceptance criteria.

    Two evaluation modes:

    * **Spec quality assessment**: scores a spec across five dimensions
      (completeness, clarity, testability, consistency, appropriateness),
      each on a 1-5 scale.
    * **Acceptance criterion assessment**: evaluates whether task outcomes
      satisfy individual acceptance criteria.

    When LLM evaluation fails, both modes fall back to rule-based heuristics
    and set ``fallback=True`` on the result.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        run_id: str = "llm-evaluator",
        trace_id: str = "llm-evaluator",
        task_id: str = "llm-evaluator",
        session_id: str = "llm-evaluator",
        workspace_id: str = "llm-evaluator",
        instance_id: str = "llm-evaluator",
        role_id: str = "LLMEvaluator",
    ) -> None:
        self._provider = provider
        self._model = model
        self._run_id = run_id
        self._trace_id = trace_id
        self._task_id = task_id
        self._session_id = session_id
        self._workspace_id = workspace_id
        self._instance_id = instance_id
        self._role_id = role_id

    async def evaluate_spec_quality(
        self,
        request: LLMEvaluationRequest,
    ) -> LLMEvaluationResult:
        """Score spec quality across five dimensions."""
        prompt = _build_spec_quality_prompt(request)
        return await self._run_evaluation(prompt)

    async def evaluate_acceptance_criteria(
        self,
        request: LLMEvaluationRequest,
    ) -> LLMEvaluationResult:
        """Assess whether task outcomes meet acceptance criteria."""
        prompt = _build_acceptance_prompt(request)
        return await self._run_evaluation(prompt)

    def as_semantic_evaluator(self) -> SemanticVerificationEvaluator:
        """Return a ``SemanticVerificationEvaluator`` protocol callable.

        The returned callable internally delegates to
        :meth:`evaluate_acceptance_criteria`.  Because the protocol is
        synchronous but the provider uses async ``generate``, the bridge
        uses :func:`asyncio.run` to drive the coroutine from a fresh event
        loop.
        """

        def _evaluator(
            semantic_request: SemanticEvaluationRequest,
        ) -> SemanticEvaluationResult:
            eval_request = LLMEvaluationRequest(
                task_id=semantic_request.task_id,
                acceptance_criteria=(semantic_request.criterion,),
                task_result=semantic_request.result_excerpt,
            )
            try:
                result = asyncio.run(
                    self.evaluate_acceptance_criteria(eval_request),
                )
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    event="llm_evaluator.semantic_bridge_failed",
                    message="LLM evaluator bridge failed, falling back",
                    payload={"error": str(exc)},
                )
                return _fallback_semantic_result(semantic_request)

            if result.fallback:
                return _fallback_semantic_result(semantic_request)

            passed = result.overall_score >= 3.5
            confidence = min(result.overall_score / 5.0, 1.0)
            return SemanticEvaluationResult(
                criterion=semantic_request.criterion,
                passed=passed,
                confidence=confidence,
                reason=result.summary,
                evaluator="llm",
            )

        return _evaluator

    async def _run_evaluation(self, prompt: str) -> LLMEvaluationResult:
        llm_request = self._build_llm_request(prompt)
        try:
            response = await self._provider.generate(llm_request)
        except Exception as exc:
            log_event(
                _LOGGER,
                logging.WARNING,
                event="llm_evaluator.generate_failed",
                message="LLM evaluation call failed, returning fallback",
                payload={"error": str(exc)},
            )
            return _fallback_evaluation_result()

        return _parse_llm_response(response)

    def _build_llm_request(self, user_prompt: str) -> LLMRequest:
        return LLMRequest(
            run_id=self._run_id,
            trace_id=self._trace_id,
            task_id=self._task_id,
            session_id=self._session_id,
            workspace_id=self._workspace_id,
            instance_id=self._instance_id,
            role_id=self._role_id,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            run_kind=RunKind.CONVERSATION,
        )


_SYSTEM_PROMPT = (
    "You are a specification quality evaluator. "
    "Respond with valid JSON only, no markdown fences."
)


def _build_spec_quality_prompt(request: LLMEvaluationRequest) -> str:
    parts = [
        "Evaluate the following specification across five dimensions: "
        "completeness, clarity, testability, consistency, appropriateness. "
        "Score each dimension 1-5.",
    ]
    if request.spec_summary:
        parts.append(f"Summary: {request.spec_summary}")
    if request.requirements:
        parts.append(
            "Requirements:\n" + "\n".join(f"- {r}" for r in request.requirements),
        )
    if request.constraints:
        parts.append(
            "Constraints:\n" + "\n".join(f"- {c}" for c in request.constraints),
        )
    if request.acceptance_criteria:
        parts.append(
            "Acceptance Criteria:\n"
            + "\n".join(f"- {ac}" for ac in request.acceptance_criteria),
        )
    if request.evidence_expectations:
        parts.append(
            "Evidence Expectations:\n"
            + "\n".join(f"- {e}" for e in request.evidence_expectations),
        )
    parts.append(
        'Respond with JSON: {"scores": [{"dimension": "...", "score": N, '
        '"reasoning": "..."}], "summary": "...", "recommendations": ["..."]}',
    )
    return "\n\n".join(parts)


def _build_acceptance_prompt(request: LLMEvaluationRequest) -> str:
    parts = [
        "Assess whether the task outcomes satisfy each acceptance criterion.",
    ]
    if request.acceptance_criteria:
        parts.append(
            "Acceptance Criteria:\n"
            + "\n".join(f"- {ac}" for ac in request.acceptance_criteria),
        )
    if request.task_result:
        parts.append(f"Task Result:\n{request.task_result}")
    parts.append(
        'Respond with JSON: {"scores": [{"dimension": "...", "score": N, '
        '"reasoning": "..."}], "summary": "...", "recommendations": ["..."]}',
    )
    return "\n\n".join(parts)


def _parse_llm_response(response: str) -> LLMEvaluationResult:
    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.index("\n")
            last_fence = cleaned.rindex("```")
            cleaned = cleaned[first_newline + 1 : last_fence].strip()

        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        log_event(
            _LOGGER,
            logging.WARNING,
            event="llm_evaluator.parse_failed",
            message="Failed to parse LLM evaluation response",
            payload={"error": str(exc), "response_preview": response[:200]},
        )
        return _fallback_evaluation_result()

    raw_scores = data.get("scores", [])
    scores: list[LLMEvaluationScore] = []
    for entry in raw_scores:
        dimension = str(entry.get("dimension", "unknown"))
        score = int(entry.get("score", 3))
        reasoning = str(entry.get("reasoning", ""))
        scores.append(
            LLMEvaluationScore(
                dimension=dimension,
                score=score,
                reasoning=reasoning,
            ),
        )

    if not scores:
        return _fallback_evaluation_result()

    overall = sum(s.score for s in scores) / len(scores)
    summary = str(data.get("summary", ""))
    recommendations = [
        str(r) for r in data.get("recommendations", []) if str(r).strip()
    ]

    return LLMEvaluationResult(
        scores=scores,
        overall_score=round(overall, 2),
        summary=summary,
        recommendations=recommendations,
    )


def _fallback_evaluation_result() -> LLMEvaluationResult:
    return LLMEvaluationResult(
        scores=[
            LLMEvaluationScore(
                dimension=dim,
                score=3,
                reasoning="LLM evaluation unavailable; using neutral fallback.",
            )
            for dim in _SPEC_QUALITY_DIMENSIONS
        ],
        overall_score=3.0,
        summary="LLM evaluation failed; fallback to rule-based assessment.",
        recommendations=[
            "Manual review recommended due to LLM evaluation failure.",
        ],
        evaluator="rule",
        fallback=True,
    )


def _fallback_semantic_result(
    request: SemanticEvaluationRequest,
) -> SemanticEvaluationResult:
    return SemanticEvaluationResult(
        criterion=request.criterion,
        passed=False,
        confidence=0.0,
        reason="LLM evaluation failed; rule-based fallback applied.",
        evaluator="rule",
    )
