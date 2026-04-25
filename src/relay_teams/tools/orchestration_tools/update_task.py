# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from relay_teams.agents.orchestration.task_contracts import TaskUpdate

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
    ) -> dict[str, JsonValue]:
        """Update a task contract that is still in the created state."""

        async def _action(
            task_id: str,
            objective: str | None = None,
            title: str | None = None,
        ) -> dict[str, JsonValue]:
            return await ctx.deps.task_service.update_task_async(
                run_id=ctx.deps.run_id,
                task_id=task_id,
                update=TaskUpdate(
                    objective=objective,
                    title=title,
                ),
            )

        return await execute_tool_call(
            ctx,
            tool_name="orch_update_task",
            args_summary={
                "task_id": task_id,
                "has_objective": objective is not None,
                "has_title": title is not None,
            },
            action=_action,
            raw_args=locals(),
        )
