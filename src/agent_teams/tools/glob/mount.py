from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.file_utils import resolve_workspace_path
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def glob(ctx: ToolContext, pattern: str) -> str:
        def _action() -> str:
            root = resolve_workspace_path(ctx.deps.workspace_root, ".")
            matches = [str(path.relative_to(root)) for path in root.rglob(pattern)]
            return "\n".join(matches[:500])

        return execute_tool(
            ctx,
            tool_name="glob",
            args_summary={"pattern": pattern},
            action=_action,
        )
