# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def dispatch_task(
        ctx: ToolContext,
        task_id: str,
        feedback: str = "",
    ) -> dict[str, JsonValue]:
        """Dispatch a created task or send follow-up feedback to a completed task."""

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
