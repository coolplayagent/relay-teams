# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LLMEvaluationScore(BaseModel):
    """A single dimension score from the LLM evaluator."""

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    score: int = Field(ge=1, le=5)
    reasoning: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, value: object) -> int:
        return max(1, min(5, int(value)))


class LLMEvaluationResult(BaseModel):
    """Aggregated result of an LLM-based spec or acceptance evaluation."""

    model_config = ConfigDict(extra="forbid")

    scores: list[LLMEvaluationScore]
    overall_score: float = Field(ge=0.0, le=5.0)
    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    evaluator: str = "llm"
    fallback: bool = False


class LLMEvaluationRequest(BaseModel):
    """Request payload for the LLM evaluator."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = ""
    spec_summary: str = ""
    requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()
    task_result: str | None = None
