from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.tools.workspace_tools.path_utils import resolve_workspace_path
from agent_teams.tools.workspace_tools import ripgrep


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def glob(
        ctx: ToolContext,
        pattern: str,
    ) -> JsonObject:
        async def _action() -> str:
            root = resolve_workspace_path(ctx.deps.workspace_root, ".")

            files, truncated = await ripgrep.enumerate_files(
                cwd=root,
                pattern=pattern,
            )

            if not files:
                return "No files found"

            rel_files = [str(f.relative_to(root)) for f in files]
            output = "\n".join(rel_files)

            if truncated:
                output += f"\n\n(Results truncated: showing first {len(files)} files)"

            return output

        return await execute_tool(
            ctx,
            tool_name="glob",
            args_summary={"pattern": pattern},
            action=_action,
        )
