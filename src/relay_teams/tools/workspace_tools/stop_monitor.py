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
from relay_teams.tools.workspace_tools.monitor_tool_support import (
    project_monitor_tool_result,
    require_monitor_service,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def stop_monitor(
        ctx: ToolContext,
        monitor_id: str,
    ) -> dict[str, JsonValue]:
        def _action():
            monitor_service = require_monitor_service(ctx)
            record = monitor_service.stop_for_run(
                run_id=ctx.deps.run_id,
                monitor_id=monitor_id,
            )
            return project_monitor_tool_result(record)

        return await execute_tool(
            ctx,
            tool_name="stop_monitor",
            args_summary={"monitor_id": monitor_id},
            action=_action,
        )
