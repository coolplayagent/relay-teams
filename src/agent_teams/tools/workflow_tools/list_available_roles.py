# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def list_available_roles(ctx: ToolContext) -> JsonObject:
        def _action() -> JsonObject:
            roles = ctx.deps.role_registry.list_roles()
            return {
                "ok": True,
                "roles": [
                    {
                        "role_id": r.role_id,
                        "name": r.name,
                        "tools": list(r.tools),
                    }
                    for r in roles
                ],
            }

        return await execute_tool(
            ctx,
            tool_name="list_available_roles",
            args_summary={},
            action=_action,
        )
