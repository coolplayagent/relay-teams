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
    output_tokens: int = 0
    total_tokens: int = 0


class EvalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    dataset: str
    intent: str
    expected_keywords: tuple[str, ...] = ()
    expected_patterns: tuple[str, ...] = ()
    reference_patch: str | None = None
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
    agent_output: str = ""
    generated_patch: str = ""
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
    mean_duration_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    results: tuple[EvalResult, ...]
