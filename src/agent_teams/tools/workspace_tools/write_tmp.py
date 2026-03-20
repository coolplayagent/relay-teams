# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.workspace_tools.path_utils import resolve_workspace_tmp_path
from agent_teams.tools.workspace_tools.write import (
    _project_write_result,
    atomic_write,
    format_diff_summary,
)

DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def write_tmp(
        ctx: ToolContext,
        path: str,
        content: str,
    ) -> dict[str, JsonValue]:
        """Write content to a file under the workspace tmp directory.

        Args:
            ctx: Tool context.
            path: Path to the file, relative to the workspace tmp directory.
            content: Content to write.
        """

        async def _action() -> ToolResultProjection:
            file_path = resolve_workspace_tmp_path(ctx.deps.workspace, path)

            old_content = ""
            created = not file_path.exists()
            if file_path.exists():
                if file_path.is_dir():
                    raise ValueError(f"Path is a directory: tmp/{path}")
                old_content = file_path.read_text(encoding="utf-8")

            diff_summary = format_diff_summary(old_content, content)
            atomic_write(file_path, content, encoding="utf-8")
            output = "Wrote tmp file successfully.\n\nDiff:\n" + diff_summary
            return _project_write_result(
                output=output,
                diff_summary=diff_summary,
                path=file_path.relative_to(ctx.deps.workspace.root_path).as_posix(),
                created=created,
            )

        return await execute_tool(
            ctx,
            tool_name="write_tmp",
            args_summary={
                "path": path,
                "content_len": len(content),
            },
            action=_action,
        )
