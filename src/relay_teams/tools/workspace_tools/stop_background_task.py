# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool
from relay_teams.tools.workspace_tools.background_task_tool_support import (
    project_background_task_tool_result,
    require_background_task_service,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def stop_background_task(
        ctx: ToolContext,
        background_task_id: str,
    ) -> dict[str, JsonValue]:
        async def _action():
            service = require_background_task_service(ctx)
            record = await service.stop_for_run(
                run_id=ctx.deps.run_id,
                background_task_id=background_task_id,
            )
            return project_background_task_tool_result(
                record,
                completed=True,
                include_task_id=True,
            )

        return await execute_tool(
            ctx,
            tool_name="stop_background_task",
            args_summary={"background_task_id": background_task_id},
            action=_action,
        )
