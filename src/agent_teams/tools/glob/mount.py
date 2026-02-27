from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.file_utils import resolve_workspace_path
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def glob(ctx, pattern: str) -> str:
        emit_tool_call(ctx, 'glob')
        root = resolve_workspace_path(ctx.deps.workspace_root, '.')
        matches = [str(path.relative_to(root)) for path in root.rglob(pattern)]
        result = with_injections(ctx, '\n'.join(matches[:500]))
        emit_tool_result(ctx, 'glob')
        return result
