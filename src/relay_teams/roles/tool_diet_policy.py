# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ToolDietSeverity(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class ToolDietFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: ToolDietSeverity
    message: str
    detail: dict[str, str | int | float | bool] = {}


class ToolDietReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: tuple[ToolDietFinding, ...]
    tool_count: int
    max_tools: int
    objective_length: int


class ToolDietPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tools_per_role: int = Field(default=10, ge=1, le=50)
    max_tools_warning_threshold: int = Field(default=7, ge=1, le=50)
    min_verification_fields: int = Field(default=1, ge=0, le=10)
    max_objective_length: int = Field(default=500, ge=50, le=5000)
    min_objective_length: int = Field(default=10, ge=1, le=100)
    broad_objective_keywords: tuple[str, ...] = (
        "everything",
        "all things",
        "anything",
        "whatever",
        "misc",
        "miscellaneous",
        "general",
        "various",
    )
