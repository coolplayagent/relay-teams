# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class SWEBenchConfig(BaseModel):
    """SWE-bench continuous tracking configuration."""

    dataset_path: Path
    max_instances: int = Field(default=10, ge=1)
    timeout_seconds_per_instance: int = Field(default=3600, ge=60)
    roles_preset: str = "swe_team"
    parallel_workers: int = Field(default=1, ge=1)
    output_dir: Path


class SWEBenchInstanceResult(BaseModel):
    """Result for a single SWE-bench instance."""

    instance_id: str
    repo: str
    resolved: bool
    patch_applied: bool
    tests_passed: int
    tests_failed: int
    duration_seconds: float
    error_message: str = ""


class SWEBenchRunResult(BaseModel):
    """Report for a single SWE-bench tracking run."""

    run_id: str
    timestamp: datetime
    config: SWEBenchConfig
    instances: tuple[SWEBenchInstanceResult, ...]
    resolved_count: int
    total_count: int
    resolve_rate: float
    total_duration_seconds: float
