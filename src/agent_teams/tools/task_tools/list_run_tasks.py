# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def list_run_tasks(
        ctx: ToolContext,
        include_root: bool = False,
    ) -> JsonObject:
        def _action() -> JsonObject:
            return ctx.deps.task_service.list_run_tasks(
                run_id=ctx.deps.run_id,
                include_root=include_root,
            )

        return await execute_tool(
            ctx,
            tool_name="list_run_tasks",
            args_summary={"include_root": include_root},
            action=_action,
        )
