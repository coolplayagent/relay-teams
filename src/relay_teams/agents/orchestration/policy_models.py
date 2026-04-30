# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_ORCHESTRATION_CYCLES = 8
DEFAULT_MAX_PARALLEL_DELEGATED_TASKS = 4
MAX_ORCHESTRATION_CYCLES_LIMIT = 64
MAX_PARALLEL_DELEGATED_TASKS_LIMIT = 16


class OrchestrationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_orchestration_cycles: int = Field(
        default=DEFAULT_MAX_ORCHESTRATION_CYCLES,
        ge=0,
        le=MAX_ORCHESTRATION_CYCLES_LIMIT,
    )
    max_parallel_delegated_tasks: int = Field(
        default=DEFAULT_MAX_PARALLEL_DELEGATED_TASKS,
        ge=0,
        le=MAX_PARALLEL_DELEGATED_TASKS_LIMIT,
    )


def build_orchestration_policy_prompt(policy: OrchestrationPolicy) -> str:
    return "\n".join(
        (
            "## Orchestration Policy",
            f"- Max orchestration cycles: {policy.max_orchestration_cycles}",
            (f"- Max parallel delegated tasks: {policy.max_parallel_delegated_tasks}"),
        )
    )
