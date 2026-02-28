from __future__ import annotations

import json

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def list_available_roles(ctx: ToolContext) -> str:
        """
        List all available roles in the system.

        Returns:
            List of roles with their role_id, name, capabilities, and available tools.
            Use this to find valid role_id values before creating custom workflows.
        """

        def _action() -> str:
            roles = ctx.deps.role_registry.list_roles()
            return json.dumps(
                {
                    "ok": True,
                    "roles": [
                        {
                            "role_id": r.role_id,
                            "name": r.name,
                            "capabilities": list(r.capabilities),
                            "depends_on": list(r.depends_on),
                            "tools": list(r.tools),
                        }
                        for r in roles
                    ],
                },
                ensure_ascii=False,
            )

        return execute_tool(
            ctx,
            tool_name="list_available_roles",
            args_summary={},
            action=_action,
        )
