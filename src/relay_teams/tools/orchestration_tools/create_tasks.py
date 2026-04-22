# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from relay_teams.agents.orchestration.task_orchestration_service import TaskDraft

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def orch_create_tasks(
        ctx: ToolContext,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]:
        """Create one or more delegated task contracts for the current run."""

        async def _action(tasks: list[TaskDraft]) -> dict[str, JsonValue]:
            return await ctx.deps.task_service.create_tasks(
                run_id=ctx.deps.run_id,
                tasks=tasks,
            )

        return await execute_tool_call(
            ctx,
            tool_name="orch_create_tasks",
            args_summary={
                "task_count": len(tasks),
            },
            action=_action,
            raw_args=locals(),
        )
