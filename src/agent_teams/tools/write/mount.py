from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.file_utils import resolve_workspace_path
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def write(ctx: ToolContext, path: str, content: str) -> str:
        def _action() -> str:
            file_path = resolve_workspace_path(ctx.deps.workspace_root, path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"WROTE:{path}"

        return execute_tool(
            ctx,
            tool_name="write",
            args_summary={"path": path, "content_len": len(content)},
            action=_action,
        )
