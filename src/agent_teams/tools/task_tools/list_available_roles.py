# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def list_available_roles(ctx: ToolContext) -> dict[str, JsonValue]:
        def _action() -> dict[str, JsonValue]:
            roles = [
                role
                for role in ctx.deps.role_registry.list_roles()
                if not ctx.deps.role_registry.is_coordinator_role(role.role_id)
            ]
            return {
                "ok": True,
                "roles": [
                    {
                        "role_id": role.role_id,
                        "name": role.name,
                        "tools": list(role.tools),
                    }
                    for role in roles
                ],
            }

        return await execute_tool(
            ctx,
            tool_name="list_available_roles",
            args_summary={},
            action=_action,
        )
