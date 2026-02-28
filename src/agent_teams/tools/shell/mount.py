from __future__ import annotations

import json

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.shell.executor import run_git_bash
from agent_teams.tools.shell.policy import normalize_timeout, validate_shell_command
from agent_teams.tools.tool_helpers import execute_tool

MAX_OUTPUT_CHARS = 64_000


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def shell(
        ctx: ToolContext, command: str, timeout_seconds: int | None = None
    ) -> str:
        def _action() -> str:
            validate_shell_command(command)
            timeout = normalize_timeout(timeout_seconds)
            code, stdout, stderr, timed_out = run_git_bash(
                command=command,
                workdir=ctx.deps.workspace_root,
                timeout_seconds=timeout,
            )
            payload = {
                "ok": code == 0,
                "exit_code": code,
                "timed_out": timed_out,
                "stdout": stdout[:MAX_OUTPUT_CHARS],
                "stderr": stderr[:MAX_OUTPUT_CHARS],
            }
            return json.dumps(payload, ensure_ascii=False)

        return execute_tool(
            ctx,
            tool_name="shell",
            args_summary={"command": command[:160], "timeout_seconds": timeout_seconds},
            action=_action,
        )
