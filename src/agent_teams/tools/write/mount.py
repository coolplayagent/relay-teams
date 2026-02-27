from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.file_utils import resolve_workspace_path
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def write(ctx, path: str, content: str) -> str:
        emit_tool_call(ctx, 'write')
        file_path = resolve_workspace_path(ctx.deps.workspace_root, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding='utf-8')
        result = with_injections(ctx, f'WROTE:{path}')
        emit_tool_result(ctx, 'write')
        return result
