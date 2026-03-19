# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.tools.workspace_tools import ripgrep

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def grep(
        ctx: ToolContext,
        pattern: str,
        path: str = ".",
        case_sensitive: bool = False,
        include: str | None = None,
    ) -> dict[str, JsonValue]:
        """Search file contents under a workspace path using a regex pattern."""

        async def _action() -> str:
            root = ctx.deps.workspace.resolve_path(path, write=False)

            result = await ripgrep.grep_search(
                cwd=root,
                pattern=pattern,
                glob=include,
                case_sensitive=case_sensitive,
            )

            return result.format()

        return await execute_tool(
            ctx,
            tool_name="grep",
            args_summary={
                "pattern": pattern,
                "path": path,
                "case_sensitive": case_sensitive,
                "include": include,
            },
            action=_action,
        )
