# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue
from pydantic_ai import Agent

from agent_teams.sessions.runs.exec_session_models import ExecSessionRecord
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
from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout
from agent_teams.tools.workspace_tools.shell_policy import validate_shell_command

CURRENT_ROLE_ENV_KEY = "AGENT_TEAMS_CURRENT_ROLE_ID"

_EXEC_COMMAND_DESCRIPTION = (
    "Run a command through the managed exec-session runtime. "
    "Short commands return inline output; long-running commands return an exec session id."
)
_LIST_DESCRIPTION = "List managed exec sessions for the current run."
_WRITE_DESCRIPTION = "Write stdin to a managed exec session. Pass empty text to long-poll for new output."
_RESIZE_DESCRIPTION = "Resize a managed TTY exec session."
_TERMINATE_DESCRIPTION = "Terminate a managed exec session."


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=_EXEC_COMMAND_DESCRIPTION)
    async def exec_command(
        ctx: ToolContext,
        command: str,
        yield_time_ms: int | None = None,
        timeout_ms: int | None = None,
        workdir: str | None = None,
        tty: bool = False,
    ) -> dict[str, JsonValue]:
        cwd = _resolve_cwd(ctx, workdir, ensure_tmp_root=False)
        approval_request = ToolApprovalRequest(
            cache_key=_build_exec_command_cache_key(
                command,
                cwd=cwd,
                tty=tty,
            ),
        )

        async def _action() -> ToolResultProjection:
            validate_shell_command(command)
            manager = _require_exec_session_manager(ctx)
            cwd = _resolve_cwd(ctx, workdir)
            timeout = normalize_timeout(timeout_ms)
            record, completed = await manager.exec_command(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id,
                workspace=ctx.deps.workspace,
                command=command,
                cwd=cwd,
                timeout_ms=timeout,
                yield_time_ms=yield_time_ms,
                env={CURRENT_ROLE_ENV_KEY: ctx.deps.role_id},
                tty=tty,
            )
            return _project_exec_session(record, completed=completed, include_id=True)

        return await execute_tool(
            ctx,
            tool_name="exec_command",
            args_summary={
                "command": command[:160],
                "yield_time_ms": yield_time_ms,
                "timeout_ms": timeout_ms,
                "workdir": workdir,
                "tty": tty,
            },
            action=_action,
            approval_request=approval_request,
        )

    @agent.tool(description=_LIST_DESCRIPTION)
    async def list_exec_sessions(ctx: ToolContext) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_exec_session_manager(ctx)
            items: list[JsonValue] = [
                cast(JsonValue, _record_payload(record))
                for record in manager.list_for_run(ctx.deps.run_id)
            ]
            payload = cast(JsonValue, {"items": items})
            return ToolResultProjection(visible_data=payload, internal_data=payload)

        return await execute_tool(
            ctx,
            tool_name="list_exec_sessions",
            args_summary={},
            action=_action,
        )

    @agent.tool(description=_WRITE_DESCRIPTION)
    async def write_stdin(
        ctx: ToolContext,
        exec_session_id: str,
        chars: str = "",
        yield_time_ms: int | None = None,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_exec_session_manager(ctx)
            record, completed = await manager.interact_for_run(
                run_id=ctx.deps.run_id,
                exec_session_id=exec_session_id,
                chars=chars,
                yield_time_ms=yield_time_ms,
            )
            projection = _project_exec_session(
                record, completed=completed, include_id=True
            )
            visible = dict(_visible_dict(projection))
            visible["wrote_chars"] = len(chars)
            internal = dict(_internal_dict(projection))
            internal["wrote_chars"] = len(chars)
            return ToolResultProjection(visible_data=visible, internal_data=internal)

        return await execute_tool(
            ctx,
            tool_name="write_stdin",
            args_summary={
                "exec_session_id": exec_session_id,
                "chars_length": len(chars),
                "yield_time_ms": yield_time_ms,
            },
            action=_action,
        )

    @agent.tool(description=_RESIZE_DESCRIPTION)
    async def resize_exec_session(
        ctx: ToolContext,
        exec_session_id: str,
        columns: int,
        rows: int,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_exec_session_manager(ctx)
            record = await manager.resize_for_run(
                run_id=ctx.deps.run_id,
                exec_session_id=exec_session_id,
                columns=columns,
                rows=rows,
            )
            projection = _project_exec_session(
                record, completed=not record.is_active, include_id=True
            )
            visible = dict(_visible_dict(projection))
            visible["columns"] = columns
            visible["rows"] = rows
            internal = dict(_internal_dict(projection))
            internal["columns"] = columns
            internal["rows"] = rows
            return ToolResultProjection(visible_data=visible, internal_data=internal)

        return await execute_tool(
            ctx,
            tool_name="resize_exec_session",
            args_summary={
                "exec_session_id": exec_session_id,
                "columns": columns,
                "rows": rows,
            },
            action=_action,
        )

    @agent.tool(description=_TERMINATE_DESCRIPTION)
    async def terminate_exec_session(
        ctx: ToolContext,
        exec_session_id: str,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_exec_session_manager(ctx)
            record = await manager.stop_for_run(
                run_id=ctx.deps.run_id,
                exec_session_id=exec_session_id,
            )
            return _project_exec_session(record, completed=True, include_id=True)

        return await execute_tool(
            ctx,
            tool_name="terminate_exec_session",
            args_summary={"exec_session_id": exec_session_id},
            action=_action,
        )


def _require_exec_session_manager(ctx: ToolContext):
    manager = ctx.deps.exec_session_manager
    if manager is None:
        raise RuntimeError("Exec session manager is not configured")
    return manager


def _build_exec_command_cache_key(
    command: str,
    *,
    cwd: Path,
    tty: bool,
) -> str:
    canonical = canonicalize_shell_command(command)
    normalized_cwd = str(cwd.resolve()).strip()
    tty_marker = "1" if tty else "0"
    return "\n".join(
        [
            f"command={canonical}",
            f"cwd={normalized_cwd}",
            f"tty={tty_marker}",
        ]
    )


def _resolve_cwd(
    ctx: ToolContext,
    workdir: str | None,
    *,
    ensure_tmp_root: bool = True,
):
    if workdir:
        cwd = ctx.deps.workspace.resolve_workdir(workdir)
    else:
        cwd = ctx.deps.workspace.resolve_workdir()
    if ensure_tmp_root and cwd == ctx.deps.workspace.tmp_root and not cwd.exists():
        cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def _record_payload(record: ExecSessionRecord) -> dict[str, JsonValue]:
    return {
        "exec_session_id": record.exec_session_id,
        "run_id": record.run_id,
        "session_id": record.session_id,
        "instance_id": record.instance_id,
        "role_id": record.role_id,
        "tool_call_id": record.tool_call_id,
        "command": record.command,
        "cwd": record.cwd,
        "execution_mode": record.execution_mode,
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
    }


def _project_exec_session(
    record: ExecSessionRecord,
    *,
    completed: bool,
    include_id: bool,
) -> ToolResultProjection:
    payload = _record_payload(record)
    payload["completed"] = completed
    if not include_id and completed:
        payload["exec_session_id"] = None
    payload["output"] = record.output_excerpt
    return ToolResultProjection(visible_data=payload, internal_data=payload)


def _visible_dict(projection: ToolResultProjection) -> dict[str, JsonValue]:
    if isinstance(projection.visible_data, dict):
        return dict(projection.visible_data)
    return {}


def _internal_dict(projection: ToolResultProjection) -> dict[str, JsonValue]:
    if isinstance(projection.internal_data, dict):
        return dict(projection.internal_data)
    return {}
