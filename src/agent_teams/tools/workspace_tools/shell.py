# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import JsonValue

import asyncio
from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.workspace_tools.shell_executor import (
    normalize_timeout,
    spawn_shell,
)
from agent_teams.tools.workspace_tools.shell_policy import validate_shell_command
from agent_teams.workspace import WorkspaceHandle

MAX_OUTPUT_CHARS = 64_000
MAX_METADATA_LENGTH = 30_000
DESCRIPTION = load_tool_description(__file__)
CURRENT_ROLE_ENV_KEY = "AGENT_TEAMS_CURRENT_ROLE_ID"


def _format_timeout_metadata(timeout_ms: int) -> str:
    return (
        "\n\n<bash_metadata>\n"
        f"Command terminated after {timeout_ms}ms timeout\n"
        "</bash_metadata>"
    )


def _save_overflow_output(
    workspace: WorkspaceHandle,
    content: str,
    label: str,
) -> Path | None:
    """Save full output to a file when it exceeds MAX_OUTPUT_CHARS.

    Returns the file path if saved, or None if no overflow occurred.
    """
    if len(content) <= MAX_OUTPUT_CHARS:
        return None
    output_dir = workspace.tmp_root / "shell_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    file_path = output_dir / f"{label}_{timestamp}.txt"
    file_path.write_text(content, encoding="utf-8")
    return file_path


def _project_shell_result(
    *,
    exit_code: int,
    timed_out: bool,
    stdout: str,
    stderr: str,
    output: str,
    stdout_overflow: Path | None,
    stderr_overflow: Path | None,
) -> ToolResultProjection:
    truncated = stdout_overflow is not None or stderr_overflow is not None
    internal_data: dict[str, JsonValue] = {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout[:MAX_OUTPUT_CHARS],
        "stderr": stderr[:MAX_OUTPUT_CHARS],
        "output": output,
    }
    if stdout_overflow:
        internal_data["stdout_overflow_path"] = str(stdout_overflow)
    if stderr_overflow:
        internal_data["stderr_overflow_path"] = str(stderr_overflow)

    return ToolResultProjection(
        visible_data={
            "output": output,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "truncated": truncated,
        },
        internal_data=internal_data,
    )


def register(Agent: Agent[ToolDeps, str]) -> None:
    @Agent.tool(description=DESCRIPTION)
    async def shell(
        ctx: ToolContext,
        command: str,
        timeout_ms: int | None = None,
        workdir: str | None = None,
        description: str | None = None,
    ) -> dict[str, JsonValue]:
        """Run a shell command in the workspace and return stdout/stderr metadata."""

        async def _action() -> ToolResultProjection:
            validate_shell_command(command)

            if workdir:
                cwd = ctx.deps.workspace.resolve_workdir(workdir)
            else:
                cwd = ctx.deps.workspace.resolve_workdir()
            if cwd == ctx.deps.workspace.tmp_root and not cwd.exists():
                cwd.mkdir(parents=True, exist_ok=True)

            timeout = normalize_timeout(timeout_ms)

            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            timed_out = False
            exit_code: int | None = None

            try:
                async for stream_type, data in spawn_shell(
                    command=command,
                    cwd=cwd,
                    timeout_ms=timeout,
                    env={CURRENT_ROLE_ENV_KEY: ctx.deps.role_id},
                ):
                    if stream_type == "stdout":
                        stdout_parts.append(data)
                    elif stream_type == "stderr":
                        stderr_parts.append(data)
                    elif stream_type == "exit_code":
                        exit_code = int(data)
            except asyncio.TimeoutError:
                timed_out = True
                exit_code = 124

            if exit_code is None:
                exit_code = 1

            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)

            stdout_overflow = _save_overflow_output(
                ctx.deps.workspace, stdout, "stdout"
            )
            stderr_overflow = _save_overflow_output(
                ctx.deps.workspace, stderr, "stderr"
            )

            output = stdout[:MAX_OUTPUT_CHARS]
            if stdout_overflow:
                stdout_reference = ctx.deps.workspace.logical_tmp_path(stdout_overflow)
                output += (
                    f"\n\n[stdout truncated: {len(stdout)} chars total. "
                    f"Full output saved to: {stdout_reference}. "
                    "Use the read or grep tool to inspect it.]"
                )

            if stderr:
                output += "\n\n[stderr]:\n" + stderr[:MAX_OUTPUT_CHARS]
                if stderr_overflow:
                    stderr_reference = ctx.deps.workspace.logical_tmp_path(
                        stderr_overflow
                    )
                    output += (
                        f"\n\n[stderr truncated: {len(stderr)} chars total. "
                        f"Full output saved to: {stderr_reference}. "
                        "Use the read or grep tool to inspect it.]"
                    )

            if timed_out:
                output += _format_timeout_metadata(timeout)

            return _project_shell_result(
                exit_code=exit_code,
                timed_out=timed_out,
                stdout=stdout,
                stderr=stderr,
                output=output,
                stdout_overflow=stdout_overflow,
                stderr_overflow=stderr_overflow,
            )

        return await execute_tool(
            ctx,
            tool_name="shell",
            args_summary={
                "command": command[:160],
                "timeout_ms": timeout_ms,
                "workdir": workdir,
            },
            action=_action,
        )
