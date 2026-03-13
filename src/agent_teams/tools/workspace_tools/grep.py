# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.tools.workspace_tools import ripgrep


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def grep(
        ctx: ToolContext,
        pattern: str,
        path: str = ".",
        case_sensitive: bool = False,
        include: str | None = None,
    ) -> dict[str, JsonValue]:
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
