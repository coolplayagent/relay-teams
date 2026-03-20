# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.agents.orchestration.task_orchestration_service import TaskUpdate

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def update_task(
        ctx: ToolContext,
        task_id: str,
        objective: str | None = None,
        title: str | None = None,
    ) -> dict[str, JsonValue]:
        """Update a task contract that is still in the created state."""

        def _action() -> dict[str, JsonValue]:
            return ctx.deps.task_service.update_task(
                run_id=ctx.deps.run_id,
                task_id=task_id,
                update=TaskUpdate(
                    objective=objective,
                    title=title,
                ),
            )

        return await execute_tool(
            ctx,
            tool_name="update_task",
            args_summary={
                "task_id": task_id,
                "has_objective": objective is not None,
                "has_title": title is not None,
            },
            action=_action,
        )
