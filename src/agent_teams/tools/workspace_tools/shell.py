# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.sessions.runs.background_tasks import BackgroundTaskService
from agent_teams.sessions.runs.background_task_models import BackgroundTaskRecord
from agent_teams.tools._description_loader import load_tool_description
from agent_teams.tools.runtime import (
    ToolApprovalRequest,
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
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
        cwd = resolve_cwd(ctx, workdir, ensure_tmp_root=False)
        approval_request = ToolApprovalRequest(
            cache_key=build_shell_cache_key(
                command,
                cwd=cwd,
                tty=tty,
                background=background,
            )
        )

        async def _action() -> ToolResultProjection:
            validate_shell_command(command)
            service = require_background_task_service(ctx)
            cwd = resolve_cwd(ctx, workdir)
            record, completed = await service.run_shell(
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
            return project_background_task(
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


def require_background_task_service(ctx: ToolContext) -> BackgroundTaskService:
    service = ctx.deps.background_task_service
    if service is None:
        raise RuntimeError("Background task service is not configured")
    return service


def build_shell_cache_key(
    command: str,
    *,
    cwd: Path,
    tty: bool,
    background: bool,
) -> str:
    canonical = canonicalize_shell_command(command)
    normalized_cwd = str(cwd.resolve()).strip()
    tty_marker = "1" if tty else "0"
    background_marker = "1" if background else "0"
    return "\n".join(
        [
            f"command={canonical}",
            f"cwd={normalized_cwd}",
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


def project_background_task(
    record: BackgroundTaskRecord,
    *,
    completed: bool,
    include_task_id: bool,
) -> ToolResultProjection:
    payload = background_task_payload(record)
    payload["completed"] = completed
    payload["output"] = record.output_excerpt
    if not include_task_id:
        payload["background_task_id"] = None
    return ToolResultProjection(visible_data=payload, internal_data=payload)


def background_task_payload(record: BackgroundTaskRecord) -> dict[str, JsonValue]:
    return {
        "background_task_id": record.exec_session_id,
        "run_id": record.run_id,
        "session_id": record.session_id,
        "instance_id": record.instance_id,
        "role_id": record.role_id,
        "tool_call_id": record.tool_call_id,
        "command": record.command,
        "cwd": record.cwd,
        "status": record.status.value,
        "tty": record.tty,
        "timeout_ms": record.timeout_ms,
        "exit_code": record.exit_code,
        "recent_output": list(record.recent_output),
        "output_excerpt": record.output_excerpt,
        "log_path": record.log_path,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at is not None else None
        ),
        "completion_notified_at": (
            record.completion_notified_at.isoformat()
            if record.completion_notified_at is not None
            else None
        ),
    }
