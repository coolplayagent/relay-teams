# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolApprovalRequest,
    ToolContext,
    ToolDeps,
    execute_tool,
)
from agent_teams.tools.workspace_tools.background_task_tool_support import (
    project_background_task_tool_result,
    require_background_task_service,
)
from agent_teams.tools.workspace_tools.command_canonicalization import (
    canonicalize_shell_command,
)
from agent_teams.tools.workspace_tools.shell_policy import validate_shell_command

CURRENT_ROLE_ENV_KEY = "AGENT_TEAMS_CURRENT_ROLE_ID"
DESCRIPTION = load_tool_description(__file__)


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def shell(
        ctx: ToolContext,
        command: str,
        background: bool = False,
        yield_time_ms: int | None = None,
        timeout_ms: int | None = None,
        workdir: str | None = None,
        tty: bool = False,
    ) -> dict[str, JsonValue]:
        approval_request = ToolApprovalRequest(
            cache_key=build_shell_cache_key(
                command,
                workdir=workdir,
                tty=tty,
                background=background,
            )
        )

        async def _action():
            validate_shell_command(command)
            service = require_background_task_service(ctx)
            cwd = resolve_cwd(ctx, workdir)
            record, completed = await service.execute_command(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
                workspace=ctx.deps.workspace,
                command=command,
                cwd=cwd,
                yield_time_ms=yield_time_ms,
                timeout_ms=timeout_ms,
                env={CURRENT_ROLE_ENV_KEY: ctx.deps.role_id},
                tty=tty,
                background=background,
            )
            return project_background_task_tool_result(
                record,
                completed=completed,
                include_task_id=background,
            )

        return await execute_tool(
            ctx,
            tool_name="shell",
            args_summary={
                "command": command[:160],
                "background": background,
                "yield_time_ms": yield_time_ms,
                "timeout_ms": timeout_ms,
                "workdir": workdir,
                "tty": tty,
            },
            action=_action,
            approval_request=approval_request,
        )


def build_shell_cache_key(
    command: str,
    *,
    workdir: str | None,
    tty: bool,
    background: bool,
) -> str:
    canonical = canonicalize_shell_command(command)
    normalized_workdir = str(workdir).strip() if workdir is not None else "<default>"
    if not normalized_workdir:
        normalized_workdir = "<default>"
    tty_marker = "1" if tty else "0"
    background_marker = "1" if background else "0"
    return "\n".join(
        [
            f"command={canonical}",
            f"workdir={normalized_workdir}",
            f"tty={tty_marker}",
            f"background={background_marker}",
        ]
    )


def resolve_cwd(
    ctx: ToolContext,
    workdir: str | None,
    *,
    ensure_tmp_root: bool = True,
) -> Path:
    if workdir:
        cwd = ctx.deps.workspace.resolve_workdir(workdir)
    else:
        cwd = ctx.deps.workspace.resolve_workdir()
    if ensure_tmp_root and cwd == ctx.deps.workspace.tmp_root and not cwd.exists():
        cwd.mkdir(parents=True, exist_ok=True)
    return cwd
