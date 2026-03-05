from __future__ import annotations

import difflib
import os
import tempfile
from pathlib import Path

from pydantic_ai import Agent

from agent_teams.core.types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.tools.file_utils import resolve_workspace_path


def generate_diff(old_path: str, old_content: str, new_content: str) -> str:
    """生成 unified diff 格式"""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines, new_lines, fromfile=old_path, tofile=old_path, lineterm=""
    )

    return "".join(diff)


def format_diff_short(old_content: str, new_content: str) -> str:
    """生成简短 diff"""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    changes = []
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


def atomic_write(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入文件"""
    file_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=file_path.parent, prefix=f".{file_path.name}.", suffix=".tmp", text=True
    )

    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)

        if os.name == "nt":
            if file_path.exists():
                os.remove(file_path)
            os.replace(temp_path, file_path)
        else:
            os.replace(temp_path, file_path)

    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def write(
        ctx: ToolContext,
        path: str,
        content: str,
    ) -> JsonObject:
        async def _action() -> str:
            file_path = resolve_workspace_path(ctx.deps.workspace_root, path)

            old_content = ""
            exists = file_path.exists()
            if exists:
                if file_path.is_dir():
                    raise ValueError(f"Path is a directory: {path}")
                old_content = file_path.read_text(encoding="utf-8")

            generate_diff(str(file_path), old_content, content)
            diff_short = format_diff_short(old_content, content)

            atomic_write(file_path, content, encoding="utf-8")

            output = "Wrote file successfully.\n\n"
            output += f"Diff:\n{diff_short}"

            return output

        return await execute_tool(
            ctx,
            tool_name="write",
            args_summary={
                "path": path,
                "content_len": len(content),
            },
            action=_action,
        )
