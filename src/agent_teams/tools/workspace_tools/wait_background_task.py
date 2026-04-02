# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.tools.workspace_tools.background_task_tool_support import (
    project_background_task_tool_result,
    require_background_task_service,
)

DESCRIPTION = load_tool_description(__file__)
DEFAULT_WAIT_MS = 30000


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def wait_background_task(
        ctx: ToolContext,
        background_task_id: str,
        wait_ms: int = DEFAULT_WAIT_MS,
    ) -> dict[str, JsonValue]:
        async def _action():
            service = require_background_task_service(ctx)
            record, completed = await service.wait_for_run(
                run_id=ctx.deps.run_id,
                background_task_id=background_task_id,
                wait_ms=wait_ms,
            )
            return project_background_task_tool_result(
                record,
                completed=completed,
                include_task_id=True,
            )

        return await execute_tool(
            ctx,
            tool_name="wait_background_task",
            args_summary={
                "background_task_id": background_task_id,
                "wait_ms": wait_ms,
            },
            action=_action,
        )
