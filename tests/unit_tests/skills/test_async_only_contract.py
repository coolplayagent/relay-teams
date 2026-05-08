# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.harnesses.execution_harness import (
    ExecutionHarness,
)
from relay_teams.agents.orchestration.harnesses.llm_harness import TaskLlmHarness
from relay_teams.agents.orchestration.harnesses.persistence_harness import (
    TaskPersistenceHarness,
)
from relay_teams.agents.orchestration.harnesses.prompt_harness import TaskPromptHarness
from relay_teams.skills.skill_routing_service import (
    SkillRoutingService,
    SkillRuntimeService,
)


def _public_callables(cls: type[object]) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and callable(value)
    }


def test_skill_runtime_shallow_sync_facades_do_not_return() -> None:
    forbidden_methods: dict[type[object], tuple[str, ...]] = {
        SkillRoutingService: ("route",),
        SkillRuntimeService: ("prepare_prompt", "route_for_role"),
        TaskLlmHarness: (
            "evaluate_completion_guard",
            "thinking_for_run",
        ),
        TaskPersistenceHarness: (
            "complete_with_assistant_error",
            "mark_runtime_after_terminal_task_update",
            "mark_runtime_idle_after_success",
            "promote_paused_runtime_lane",
            "promote_running_runtime_lane",
            "record_memory_if_needed",
        ),
        TaskPromptHarness: (
            "build_user_prompt",
            "conversation_context_for_run",
            "ensure_committed_task_prompt",
            "shared_state_snapshot",
            "topology_for_run",
        ),
        ExecutionHarness: (
            "build_user_prompt",
            "complete_with_assistant_error",
            "conversation_context_for_run",
            "ensure_committed_task_prompt",
            "evaluate_completion_guard",
            "mark_runtime_after_terminal_task_update",
            "mark_runtime_idle_after_success",
            "promote_paused_runtime_lane",
            "promote_running_runtime_lane",
            "record_memory_if_needed",
            "shared_state_snapshot",
            "thinking_for_run",
            "topology_for_run",
        ),
    }

    for cls, method_names in forbidden_methods.items():
        methods = _public_callables(cls)
        returned_methods = sorted(name for name in method_names if name in methods)
        assert returned_methods == []
