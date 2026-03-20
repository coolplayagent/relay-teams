from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    TIMEOUT = "timeout"


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    total_tool_calls: int = 0


class AuxiliaryScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    passed: bool | None = None
    detail: str = ""


class EvalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    dataset: str
    intent: str
    expected_keywords: tuple[str, ...] = ()
    expected_patterns: tuple[str, ...] = ()
    reference_patch: str | None = None
    test_patch: str | None = None
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    repo_url: str | None = None
    base_commit: str | None = None
    extra_fields: dict[str, str] = Field(default_factory=dict)


class EvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    dataset: str
    run_id: str
    session_id: str
    outcome: RunOutcome
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    scorer_name: str
    scorer_detail: str = ""
    scorer_log: str = ""
    auxiliary_scores: dict[str, AuxiliaryScore] = Field(default_factory=dict)
    agent_output: str = ""
    generated_patch: str = ""
    raw_generated_patch: str = ""
    filtered_generated_files: tuple[str, ...] = ()
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    duration_seconds: float = 0.0
    workspace_path: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    error: str | None = None


class EvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    scorer_name: str
    total: int
    passed: int
    failed: int
    errored: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    mean_score: float
    auxiliary_score_means: dict[str, float] = Field(default_factory=dict)
    mean_duration_seconds: float
    p50_duration_seconds: float = 0.0
    p95_duration_seconds: float = 0.0
    outcome_completed: int = 0
    outcome_failed: int = 0
    outcome_timed_out: int = 0
    outcome_stopped: int = 0
    total_input_tokens: int
    total_cached_input_tokens: int
    total_output_tokens: int
    total_reasoning_output_tokens: int
    total_requests: int
    total_tool_calls: int
    estimated_input_cost_usd: float = 0.0
    estimated_cached_input_cost_usd: float = 0.0
    estimated_output_cost_usd: float = 0.0
    estimated_reasoning_output_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    results: tuple[EvalResult, ...]
