# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool_call,
)
from relay_teams.tools.workspace_tools.path_utils import resolve_workspace_tmp_path
from relay_teams.tools.workspace_tools.write import (
    atomic_write,
    format_diff_summary,
)

_TMP_PREFIXES = ("tmp/", "tmp\\")
DESCRIPTION = load_tool_description(__file__)


def _normalize_tmp_relative_path(path: str) -> str:
    normalized_path = str(path).strip()
    if normalized_path == "tmp":
        raise ValueError("Path must point to a file inside the workspace tmp directory")
    if normalized_path.startswith(_TMP_PREFIXES):
        normalized_path = normalized_path.removeprefix("tmp").lstrip("/\\")
    if not normalized_path:
        raise ValueError("Path must point to a file inside the workspace tmp directory")
    return normalized_path


def _project_write_tmp_result(
    *,
    output: str,
    diff_summary: str,
    path: str,
    created: bool,
) -> ToolResultProjection:
    return ToolResultProjection(
        visible_data={"output": output},
        internal_data={
            "output": output,
            "diff_summary": diff_summary,
            "path": path,
            "created": created,
        },
    )


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def write_tmp(
        ctx: ToolContext,
        path: str,
        content: str,
    ) -> dict[str, JsonValue]:
        async def _action(path: str, content: str) -> ToolResultProjection:
            relative_tmp_path = _normalize_tmp_relative_path(path)
            file_path = resolve_workspace_tmp_path(
                ctx.deps.workspace,
                relative_tmp_path,
            )

            old_content = ""
            created = not file_path.exists()
            if file_path.exists():
                if file_path.is_dir():
                    raise ValueError(f"Path is a directory: tmp/{relative_tmp_path}")
                old_content = file_path.read_text(encoding="utf-8")

            diff_summary = format_diff_summary(old_content, content)
            atomic_write(file_path, content, encoding="utf-8")
            output = "Wrote tmp file successfully.\n\nDiff:\n" + diff_summary
            logical_path = Path("tmp", relative_tmp_path).as_posix()
            return _project_write_tmp_result(
                output=output,
                diff_summary=diff_summary,
                path=logical_path,
                created=created,
            )

        return await execute_tool_call(
            ctx,
            tool_name="write_tmp",
            args_summary={
                "path": path,
                "content_len": len(content),
            },
            action=_action,
            raw_args=locals(),
        )
