# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.coordination.task_orchestration_service import TaskUpdate
from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def update_task(
        ctx: ToolContext,
        task_id: str,
        role_id: str | None = None,
        objective: str | None = None,
        title: str | None = None,
    ) -> JsonObject:
        def _action() -> JsonObject:
            return ctx.deps.task_service.update_task(
                run_id=ctx.deps.run_id,
                task_id=task_id,
                update=TaskUpdate(
                    role_id=role_id,
                    objective=objective,
                    title=title,
                ),
            )

        return await execute_tool(
            ctx,
            tool_name="update_task",
            args_summary={
                "task_id": task_id,
                "has_role_id": role_id is not None,
                "has_objective": objective is not None,
                "has_title": title is not None,
            },
            action=_action,
        )
