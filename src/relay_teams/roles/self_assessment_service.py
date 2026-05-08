# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator
from relay_teams.agents.orchestration.llm_evaluator_models import LLMEvaluationResult
from relay_teams.roles.memory_models import RolePerformanceMetrics
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.validation import RequiredIdentifierStr


class SelfAssessmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_every_n_runs: int = Field(default=10, ge=1)
    enabled: bool = True
    min_tasks_for_assessment: int = Field(default=5, ge=1)


class PromptAdjustmentRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_section: str = Field(min_length=1)
    current_text: str = ""
    recommended_text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    priority: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)


class SelfAssessmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    generated_at: datetime
    overall_assessment: str = Field(min_length=1)
    recommendations: tuple[PromptAdjustmentRecommendation, ...] = ()
    metrics_snapshot: RolePerformanceMetrics
    assessment_version: int = Field(default=1, ge=1)


class RoleSelfAssessmentService:
    def __init__(
        self,
        *,
        llm_evaluator: LLMEvaluator,
        role_memory_service: RoleMemoryService,
        config: SelfAssessmentConfig | None = None,
    ) -> None:
        self._llm_evaluator = llm_evaluator
        self._role_memory_service = role_memory_service
        self._config = config if config is not None else SelfAssessmentConfig()

    async def maybe_assess(
        self,
        *,
        role_id: str,
        workspace_id: str,
        current_system_prompt: str,
        run_count_since_last: int,
    ) -> SelfAssessmentResult | None:
        if not self._config.enabled:
            return None

        if run_count_since_last < self._config.trigger_every_n_runs:
            return None

        record = await self._role_memory_service.get_reflection_record_async(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        if record.performance is None:
            return None
        if (
            record.performance.task_counts.total_tasks
            < self._config.min_tasks_for_assessment
        ):
            return None

        eval_result: LLMEvaluationResult
        try:
            eval_result = await self._llm_evaluator.evaluate_role_performance(
                role_id=role_id,
                current_system_prompt=current_system_prompt,
                performance=record.performance,
            )
        except (ValueError, KeyError, TypeError, OSError):
            eval_result = _fallback_performance_result()

        recommendations = _parse_recommendations(eval_result)
        generated_at = datetime.now(tz=timezone.utc)
        overall_assessment = eval_result.summary or "Self-assessment completed."

        return SelfAssessmentResult(
            role_id=role_id,
            workspace_id=workspace_id,
            generated_at=generated_at,
            overall_assessment=overall_assessment,
            recommendations=recommendations,
            metrics_snapshot=record.performance,
        )


def _parse_recommendations(
    result: LLMEvaluationResult,
) -> tuple[PromptAdjustmentRecommendation, ...]:
    recommendations: list[PromptAdjustmentRecommendation] = []
    for rec_text in result.recommendations:
        if not rec_text.strip():
            continue
        recommendations.append(
            PromptAdjustmentRecommendation(
                target_section="strategy",
                current_text="",
                recommended_text=rec_text,
                rationale="LLM-generated recommendation from self-assessment",
                priority=3,
                confidence=0.5,
            )
        )
    return tuple(recommendations)


def _fallback_performance_result() -> LLMEvaluationResult:
    return LLMEvaluationResult(
        scores=[],
        overall_score=3.0,
        summary="LLM evaluation unavailable; fallback to rule-based self-assessment.",
        recommendations=[
            "Review verification pass rate trends and consider adjusting spec clarity.",
        ],
        evaluator="rule",
        fallback=True,
    )
