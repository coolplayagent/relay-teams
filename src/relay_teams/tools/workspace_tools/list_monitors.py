# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from relay_teams.tools.workspace_tools.monitor_tool_support import (
    require_monitor_service,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def list_monitors(ctx: ToolContext) -> dict[str, JsonValue]:
        def _action() -> ToolResultProjection:
            monitor_service = require_monitor_service(ctx)
            items: list[JsonValue] = [
                record.model_dump(mode="json")
                for record in monitor_service.list_for_run(ctx.deps.run_id)
            ]
            payload: dict[str, JsonValue] = {"items": items}
            return ToolResultProjection(visible_data=payload, internal_data=payload)

        return await execute_tool(
            ctx,
            tool_name="list_monitors",
            args_summary={},
            action=_action,
        )
