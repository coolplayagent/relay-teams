# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MetricKind(str, Enum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"


class MetricScope(str, Enum):
    GLOBAL = "global"
    SESSION = "session"
    RUN = "run"


class MetricTagSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = ""
    session_id: str = ""
    run_id: str = ""
    instance_id: str = ""
    role_id: str = ""
    tool_name: str = ""
    tool_source: str = ""
    mcp_server: str = ""
    status: str = ""

    def normalized_items(self) -> tuple[tuple[str, str], ...]:
        items = (
            ("workspace_id", self.workspace_id),
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("instance_id", self.instance_id),
            ("role_id", self.role_id),
            ("tool_name", self.tool_name),
            ("tool_source", self.tool_source),
            ("mcp_server", self.mcp_server),
            ("status", self.status),
        )
        return tuple((key, value) for key, value in items if value)


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    kind: MetricKind
    description: str = Field(min_length=1)
    unit: str = ""


class MetricEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_name: str = Field(min_length=1)
    kind: MetricKind
    value: float
    tags: MetricTagSet = Field(default_factory=MetricTagSet)
    occurred_at: datetime


class MetricsScopeSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MetricScope
    scope_id: str = ""
    time_window_minutes: int = Field(default=1440, ge=1)


class ObservabilityKpiSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: float = 0
    input_tokens: float = 0
    cached_input_tokens: float = 0
    output_tokens: float = 0
    cached_token_ratio: float = 0
    tool_calls: float = 0
    tool_success_rate: float = 0
    tool_avg_duration_ms: float = 0
    skill_calls: float = 0
    mcp_calls: float = 0


class ObservabilityTrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bucket_start: str = Field(min_length=1)
    steps: float = 0
    input_tokens: float = 0
    output_tokens: float = 0
    tool_calls: float = 0


class ObservabilityBreakdownRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1)
    tool_source: str = ""
    mcp_server: str = ""
    calls: float = 0
    failures: float = 0
    success_rate: float = 0
    avg_duration_ms: float = 0


class ObservabilityOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MetricScope
    scope_id: str = ""
    time_window_minutes: int = Field(ge=1)
    updated_at: str | None = None
    kpis: ObservabilityKpiSet
    trends: tuple[ObservabilityTrendPoint, ...] = ()


class ObservabilityBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MetricScope
    scope_id: str = ""
    time_window_minutes: int = Field(ge=1)
    updated_at: str | None = None
    rows: tuple[ObservabilityBreakdownRow, ...] = ()
