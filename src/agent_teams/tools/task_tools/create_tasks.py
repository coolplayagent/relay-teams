# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.agents.orchestration.task_orchestration_service import TaskDraft
from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def create_tasks(
        ctx: ToolContext,
        tasks: list[TaskDraft],
        auto_dispatch: bool = False,
    ) -> JsonObject:
        async def _action() -> JsonObject:
            return await ctx.deps.task_service.create_tasks(
                run_id=ctx.deps.run_id,
                tasks=tasks,
                auto_dispatch=auto_dispatch,
            )

        return await execute_tool(
            ctx,
            tool_name="create_tasks",
            args_summary={
                "task_count": len(tasks),
                "auto_dispatch": auto_dispatch,
            },
            action=_action,
        )
