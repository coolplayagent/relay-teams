# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.workspace_tools.shell import (
    background_task_payload,
    require_background_task_service,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def list_background_tasks(ctx: ToolContext) -> dict[str, JsonValue]:
        def _action() -> ToolResultProjection:
            service = require_background_task_service(ctx)
            items: list[JsonValue] = [
                background_task_payload(record)
                for record in service.list_for_run(ctx.deps.run_id)
            ]
            payload: dict[str, JsonValue] = {"items": items}
            return ToolResultProjection(visible_data=payload, internal_data=payload)

        return await execute_tool(
            ctx,
            tool_name="list_background_tasks",
            args_summary={},
            action=_action,
        )
