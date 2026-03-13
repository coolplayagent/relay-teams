# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import asyncio
from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.tools.workspace_tools.shell_executor import (
    normalize_timeout,
    spawn_shell,
)
from agent_teams.tools.workspace_tools.shell_policy import validate_shell_command

MAX_OUTPUT_CHARS = 64_000
MAX_METADATA_LENGTH = 30_000


def register(Agent: Agent[ToolDeps, str]) -> None:
    @Agent.tool
    async def shell(
        ctx: ToolContext,
        command: str,
        timeout_ms: int | None = None,
        workdir: str | None = None,
        description: str | None = None,
    ) -> dict[str, JsonValue]:
        async def _action() -> dict[str, JsonValue]:
            validate_shell_command(command)

            if workdir:
                cwd = ctx.deps.workspace.resolve_workdir(workdir)
            else:
                cwd = ctx.deps.workspace.resolve_workdir()

            timeout = normalize_timeout(timeout_ms)

            stdout_parts = []
            stderr_parts = []
            timed_out = False
            exit_code = 0

            try:
                async for stream_type, data in spawn_shell(
                    command=command,
                    cwd=cwd,
                    timeout_ms=timeout,
                ):
                    if stream_type == "stdout":
                        stdout_parts.append(data)
                    else:
                        stderr_parts.append(data)
            except asyncio.TimeoutError:
                timed_out = True
                exit_code = 124

            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)

            if not timed_out and stdout_parts:
                exit_code = 0

            output = stdout[:MAX_OUTPUT_CHARS]
            if stderr:
                output += "\n\n[stderr]:\n" + stderr[:MAX_OUTPUT_CHARS]

            if timed_out:
                output += f"\n\n<bash_metadata>\nCommand terminated after {timeout_ms}ms timeout\n</bash_metadata>"

            return {
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout": stdout[:MAX_OUTPUT_CHARS],
                "stderr": stderr[:MAX_OUTPUT_CHARS],
                "output": output,
            }

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
