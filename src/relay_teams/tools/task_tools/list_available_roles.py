# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import ToolContext, ToolDeps, execute_tool

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def list_available_roles(ctx: ToolContext) -> dict[str, JsonValue]:
        """List worker roles that can be selected for delegated tasks."""

        def _action() -> dict[str, JsonValue]:
            source_roles = (
                ctx.deps.runtime_role_resolver.list_effective_roles(
                    run_id=ctx.deps.run_id
                )
                if ctx.deps.runtime_role_resolver is not None
                else ctx.deps.role_registry.list_roles()
            )
            roles = [
                role
                for role in source_roles
                if not ctx.deps.role_registry.is_coordinator_role(role.role_id)
                and not ctx.deps.role_registry.is_main_agent_role(role.role_id)
            ]
            return {
                "roles": [
                    {
                        "role_id": role.role_id,
                        "name": role.name,
                        "tools": list(role.tools),
                        "source": (
                            "static"
                            if any(
                                r.role_id == role.role_id
                                for r in ctx.deps.role_registry.list_roles()
                            )
                            else "temporary"
                        ),
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
