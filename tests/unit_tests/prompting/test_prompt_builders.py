# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.prompting.provider_augment import (
    PromptSkillInstruction,
    ProviderPromptAugmentInput,
    build_provider_augmented_system_prompt,
)
from agent_teams.prompting.runtime import (
    RuntimePromptBuildInput,
    build_runtime_system_prompt,
)
from agent_teams.prompting.user_input import UserPromptBuildInput, build_user_prompt
from agent_teams.roles.models import RoleDefinition
from agent_teams.workflow.models import TaskEnvelope, VerificationPlan


def _role(role_id: str) -> RoleDefinition:
    return RoleDefinition(
        role_id=role_id,
        name="role",
        version="1",
        tools=(),
        mcp_servers=(),
        skills=(),
        model_profile="default",
        system_prompt="You are a focused agent.",
    )


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="trace-1",
        objective="Deliver weekly summary",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )


def test_runtime_system_prompt_for_coordinator_has_contract_and_context() -> None:
    prompt = build_runtime_system_prompt(
        RuntimePromptBuildInput(
            role=_role("coordinator_agent"),
            task=_task(),
            shared_state_snapshot=(("status", "ready"),),
        )
    )

    assert "## Role" in prompt
    assert "## Runtime Contract" in prompt
    assert "list_run_tasks and dispatch_task results" in prompt
    assert "- TaskRef: task-1" in prompt
    assert "- status: ready" in prompt
    assert "Deliver weekly summary" not in prompt


def test_runtime_system_prompt_for_worker_skips_runtime_contract() -> None:
    prompt = build_runtime_system_prompt(
        RuntimePromptBuildInput(
            role=_role("writer_agent"),
            task=_task(),
            shared_state_snapshot=(),
        )
    )

    assert "## Runtime Contract" not in prompt
    assert "## Shared State" in prompt
    assert "- none" in prompt


def test_provider_augmented_system_prompt_renders_tools_and_skills() -> None:
    prompt = build_provider_augmented_system_prompt(
        ProviderPromptAugmentInput(
            system_prompt="## Role\nYou are a planner.",
            allowed_tools=("dispatch_task",),
            skill_instructions=(
                PromptSkillInstruction(
                    name="time",
                    instructions="Always normalize to UTC.",
                ),
            ),
        )
    )

    assert "## Tool Rules" in prompt
    assert "dispatch_task" in prompt
    assert "## Skill Instructions" in prompt
    assert "### Skill: time" in prompt
    assert "Always normalize to UTC." in prompt


def test_user_prompt_builder_returns_raw_objective() -> None:
    prompt = build_user_prompt(
        UserPromptBuildInput(objective="Draft the release notes.")
    )

    assert prompt == "Draft the release notes."


def test_runtime_system_prompt_for_coordinator_mentions_task_orchestration() -> None:
    prompt = build_runtime_system_prompt(
        RuntimePromptBuildInput(
            role=_role("coordinator_agent"),
            task=_task(),
            shared_state_snapshot=(),
        )
    )

    assert (
        "Create tasks only when delegation is necessary; otherwise answer directly."
        in prompt
    )
    assert "list_run_tasks" in prompt
    assert "dispatch_task" in prompt
