# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def list_available_roles(ctx: ToolContext) -> dict[str, JsonValue]:
        """List worker roles that can be selected for delegated tasks."""

        def _action() -> dict[str, JsonValue]:
            roles = [
                role
                for role in ctx.deps.role_registry.list_roles()
                if not ctx.deps.role_registry.is_coordinator_role(role.role_id)
            ]
            return {
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
