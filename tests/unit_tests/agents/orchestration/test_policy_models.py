# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.policy_models import (
    OrchestrationPolicy,
    build_orchestration_policy_prompt,
)


def test_orchestration_policy_defaults_to_legacy_limits() -> None:
    policy = OrchestrationPolicy()

    assert policy.max_orchestration_cycles == 8
    assert policy.max_parallel_delegated_tasks == 4
    assert policy.auto_plan_long_tasks is True
    assert policy.planner_role_id == "DelegationPlanner"
    assert policy.coordinator_inline_budget_steps == 2
    assert policy.max_temporary_roles_per_run == 5
    assert policy.prefer_temporary_roles_for_long_tasks is True


def test_orchestration_policy_allows_zero_for_simple_runs() -> None:
    policy = OrchestrationPolicy(
        max_orchestration_cycles=0,
        max_parallel_delegated_tasks=0,
    )

    assert policy.max_orchestration_cycles == 0
    assert policy.max_parallel_delegated_tasks == 0


def test_orchestration_policy_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        OrchestrationPolicy(max_orchestration_cycles=65)

    with pytest.raises(ValueError):
        OrchestrationPolicy(max_parallel_delegated_tasks=17)

    with pytest.raises(ValueError):
        OrchestrationPolicy(coordinator_inline_budget_steps=17)

    with pytest.raises(ValueError):
        OrchestrationPolicy(max_temporary_roles_per_run=17)


def test_build_orchestration_policy_prompt_includes_limits() -> None:
    prompt = build_orchestration_policy_prompt(
        OrchestrationPolicy(
            max_orchestration_cycles=16,
            max_parallel_delegated_tasks=8,
        )
    )

    assert "## Orchestration Policy" in prompt
    assert "Max orchestration cycles: 16" in prompt
    assert "Max parallel delegated tasks: 8" in prompt
    assert "Planner role: DelegationPlanner" in prompt
