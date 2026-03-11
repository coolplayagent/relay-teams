# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def dispatch_task(
        ctx: ToolContext,
        task_id: str,
        feedback: str = "",
    ) -> JsonObject:
        return await execute_tool(
            ctx,
            tool_name="dispatch_task",
            args_summary={
                "task_id": task_id,
                "feedback_len": len(feedback),
            },
            action=lambda: ctx.deps.task_service.dispatch_task(
                run_id=ctx.deps.run_id,
                task_id=task_id,
                feedback=feedback,
            ),
        )
