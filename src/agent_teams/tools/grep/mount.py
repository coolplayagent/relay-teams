from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent

from agent_teams.tools.file_utils import resolve_workspace_path
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import emit_tool_call, emit_tool_result, with_injections


MAX_FILE_SIZE = 256_000


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def grep(ctx, pattern: str, path: str = '.', case_sensitive: bool = False) -> str:
        emit_tool_call(ctx, 'grep')
        root = resolve_workspace_path(ctx.deps.workspace_root, path)
        needle = pattern if case_sensitive else pattern.lower()
        hits: list[str] = []
        for file_path in root.rglob('*'):
            if not file_path.is_file() or file_path.stat().st_size > MAX_FILE_SIZE:
                continue
            try:
                text = file_path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            lines = text.splitlines()
            for idx, line in enumerate(lines, start=1):
                hay = line if case_sensitive else line.lower()
                if needle in hay:
                    rel = file_path.relative_to(ctx.deps.workspace_root.resolve())
                    hits.append(f'{rel}:{idx}:{line.strip()}')
                    if len(hits) >= 500:
                        break
            if len(hits) >= 500:
                break
        result = with_injections(ctx, '\n'.join(hits))
        emit_tool_result(ctx, 'grep')
        return result
