# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import tempfile
from pathlib import Path

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.paths import (
    make_dirs,
    path_exists,
    path_is_dir,
    read_text_file,
    replace_path,
    to_filesystem_path,
    unlink_path,
)
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool_call,
)


def generate_diff(old_path: str, old_content: str, new_content: str) -> str:
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_path,
            tofile=old_path,
            lineterm="",
        )
    )


def format_diff_summary(old_content: str, new_content: str) -> str:
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    changes: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            changes.append(f"  ~ {i1 + 1}: {j2 - j1} line(s) changed")
        elif tag == "delete":
            changes.append(f"  - {i1 + 1}-{i2}: {i2 - i1} line(s) deleted")
        elif tag == "insert":
            changes.append(f"  + {j1 + 1}: {j2 - j1} line(s) added")

    return "\n".join(changes) if changes else "No changes"


def format_diff_short(old_content: str, new_content: str) -> str:
    return format_diff_summary(old_content, new_content)


def atomic_write(
    file_path: Path,
    content: str,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    make_dirs(file_path.parent, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        newline=newline,
        delete=False,
        dir=to_filesystem_path(file_path.parent),
        prefix=f".{file_path.name}.",
        suffix=".tmp",
    ) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    try:
        replace_path(temp_path, file_path)
    except OSError:
        unlink_path(temp_path, missing_ok=True)
        raise


DESCRIPTION = load_tool_description(__file__)


def _project_write_result(
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
    async def write(
        ctx: ToolContext,
        path: str,
        content: str,
    ) -> dict[str, JsonValue]:
        """Write content to a file.

        Args:
            ctx: Tool context.
            path: Path to the file, relative to the workspace root.
            content: Content to write.
        """

        async def _action(path: str, content: str) -> ToolResultProjection:
            file_path = ctx.deps.workspace.resolve_path(path, write=True)

            old_content = ""
            created = not path_exists(file_path)
            if path_exists(file_path):
                if path_is_dir(file_path):
                    raise ValueError(f"Path is a directory: {path}")
                old_content = read_text_file(file_path)

            diff_summary = format_diff_summary(old_content, content)
            atomic_write(file_path, content, encoding="utf-8")
            output = "Wrote file successfully.\n\nDiff:\n" + diff_summary
            return _project_write_result(
                output=output,
                diff_summary=diff_summary,
                path=path,
                created=created,
            )

        return await execute_tool_call(
            ctx,
            tool_name="write",
            args_summary={
                "path": path,
                "content_len": len(content),
            },
            action=_action,
            raw_args=locals(),
        )
