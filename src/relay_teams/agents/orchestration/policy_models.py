# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_ORCHESTRATION_CYCLES = 8
DEFAULT_MAX_PARALLEL_DELEGATED_TASKS = 4
MAX_ORCHESTRATION_CYCLES_LIMIT = 64
MAX_PARALLEL_DELEGATED_TASKS_LIMIT = 16
DEFAULT_PLANNER_ROLE_ID = "DelegationPlanner"
DEFAULT_COORDINATOR_INLINE_BUDGET_STEPS = 2
DEFAULT_MAX_TEMPORARY_ROLES_PER_RUN = 5
MAX_TEMPORARY_ROLES_PER_RUN_LIMIT = 16


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
    auto_plan_long_tasks: bool = True
    planner_role_id: str = Field(default=DEFAULT_PLANNER_ROLE_ID, min_length=1)
    coordinator_inline_budget_steps: int = Field(
        default=DEFAULT_COORDINATOR_INLINE_BUDGET_STEPS,
        ge=0,
        le=16,
    )
    max_temporary_roles_per_run: int = Field(
        default=DEFAULT_MAX_TEMPORARY_ROLES_PER_RUN,
        ge=0,
        le=MAX_TEMPORARY_ROLES_PER_RUN_LIMIT,
    )
    prefer_temporary_roles_for_long_tasks: bool = True


def build_orchestration_policy_prompt(policy: OrchestrationPolicy) -> str:
    return "\n".join(
        (
            "## Orchestration Policy",
            f"- Max orchestration cycles: {policy.max_orchestration_cycles}",
            f"- Max parallel delegated tasks: {policy.max_parallel_delegated_tasks}",
            f"- Auto plan long tasks: {policy.auto_plan_long_tasks}",
            f"- Planner role: {policy.planner_role_id}",
            (
                "- Coordinator inline budget steps: "
                f"{policy.coordinator_inline_budget_steps}"
            ),
            f"- Max temporary roles per run: {policy.max_temporary_roles_per_run}",
            (
                "- Prefer temporary roles for long tasks: "
                f"{policy.prefer_temporary_roles_for_long_tasks}"
            ),
        )
    )
