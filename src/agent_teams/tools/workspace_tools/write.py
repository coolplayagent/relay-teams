# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import difflib
import tempfile
from pathlib import Path

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool


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


def atomic_write(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        delete=False,
        dir=file_path.parent,
        prefix=f".{file_path.name}.",
        suffix=".tmp",
    ) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    try:
        temp_path.replace(file_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
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

        async def _action() -> str:
            file_path = ctx.deps.workspace.resolve_path(path, write=True)

            old_content = ""
            if file_path.exists():
                if file_path.is_dir():
                    raise ValueError(f"Path is a directory: {path}")
                old_content = file_path.read_text(encoding="utf-8")

            diff_summary = format_diff_summary(old_content, content)
            atomic_write(file_path, content, encoding="utf-8")
            return "Wrote file successfully.\n\nDiff:\n" + diff_summary

        return await execute_tool(
            ctx,
            tool_name="write",
            args_summary={
                "path": path,
                "content_len": len(content),
            },
            action=_action,
        )
