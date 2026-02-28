from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.file_utils import resolve_workspace_path
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


MAX_CHARS = 50_000


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def read(ctx: ToolContext, path: str) -> str:
        def _action() -> str:
            file_path = resolve_workspace_path(ctx.deps.workspace_root, path)
            if not file_path.exists() or not file_path.is_file():
                raise ValueError(f"Not a file: {path}")
            text = file_path.read_text(encoding="utf-8")
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS]
            return text

        return execute_tool(
            ctx,
            tool_name="read",
            args_summary={"path": path},
            action=_action,
        )
