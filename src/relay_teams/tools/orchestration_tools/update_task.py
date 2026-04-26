# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from relay_teams.agents.orchestration.task_contracts import TaskUpdate
from relay_teams.agents.tasks.models import (
    TaskHandoff,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationPlan,
)

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def orch_update_task(
        ctx: ToolContext,
        task_id: str,
        objective: str | None = None,
        title: str | None = None,
        spec: TaskSpec | None = None,
        verification: VerificationPlan | None = None,
        lifecycle: TaskLifecyclePolicy | None = None,
        handoff: TaskHandoff | None = None,
    ) -> dict[str, JsonValue]:
        """Update a task contract that is still in the created state."""

        async def _action(tool_input: dict[str, JsonValue]) -> dict[str, JsonValue]:
            task_id_value = tool_input.get("task_id")
            if task_id_value is None:
                raise ValueError("task_id is required")
            spec_value = tool_input.get("spec")
            verification_value = tool_input.get("verification")
            lifecycle_value = tool_input.get("lifecycle")
            handoff_value = tool_input.get("handoff")
            return await ctx.deps.task_service.update_task_async(
                run_id=ctx.deps.run_id,
                task_id=str(task_id_value),
                update=TaskUpdate(
                    objective=_optional_text(tool_input.get("objective")),
                    title=_optional_text(tool_input.get("title")),
                    spec=(
                        None
                        if spec_value is None
                        else TaskSpec.model_validate(spec_value)
                    ),
                    verification=(
                        None
                        if verification_value is None
                        else VerificationPlan.model_validate(verification_value)
                    ),
                    lifecycle=(
                        None
                        if lifecycle_value is None
                        else TaskLifecyclePolicy.model_validate(lifecycle_value)
                    ),
                    handoff=(
                        None
                        if handoff_value is None
                        else TaskHandoff.model_validate(handoff_value)
                    ),
                ),
            )

        return await execute_tool_call(
            ctx,
            tool_name="orch_update_task",
            args_summary={
                "task_id": task_id,
                "has_objective": objective is not None,
                "has_title": title is not None,
                "has_spec": spec is not None,
                "has_verification": verification is not None,
                "has_lifecycle": lifecycle is not None,
                "has_handoff": handoff is not None,
            },
            action=_action,
            raw_args=locals(),
        )


def _optional_text(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    return str(value)
