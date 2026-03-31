# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from pydantic_ai import Agent

from agent_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from agent_teams.tools.workspace_tools.shell import CURRENT_ROLE_ENV_KEY
from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout
from agent_teams.tools.workspace_tools.shell_policy import validate_shell_command
from agent_teams.sessions.runs.background_terminal_models import (
    BackgroundTerminalRecord,
)

_START_DESCRIPTION = (
    "Start a managed background shell terminal bound to the current run and "
    "return a terminal id for later polling or control."
)
_LIST_DESCRIPTION = "List managed background shell terminals for the current run."
_READ_DESCRIPTION = (
    "Read the current status and recent output for a managed background shell terminal."
)
_WAIT_DESCRIPTION = "Wait briefly for a managed background shell terminal to finish and return its updated status."
_WRITE_DESCRIPTION = "Write stdin to a managed background shell terminal. Pass empty text to poll its latest state."
_RESIZE_DESCRIPTION = "Resize a managed TTY background shell terminal."
_STOP_DESCRIPTION = "Stop a managed background shell terminal."


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=_START_DESCRIPTION)
    async def shell_background_start(
        ctx: ToolContext,
        command: str,
        timeout_ms: int | None = None,
        workdir: str | None = None,
        tty: bool = False,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            validate_shell_command(command)
            manager = _require_background_terminal_manager(ctx)
            cwd = _resolve_cwd(ctx, workdir)
            timeout = normalize_timeout(timeout_ms)
            record = await manager.start_terminal(
                run_id=ctx.deps.run_id,
                session_id=ctx.deps.session_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_call_id=ctx.tool_call_id or "",
                workspace=ctx.deps.workspace,
                command=command,
                cwd=cwd,
                timeout_ms=timeout,
                env={CURRENT_ROLE_ENV_KEY: ctx.deps.role_id},
                tty=tty,
            )
            return _project_terminal_record(record)

        return await execute_tool(
            ctx,
            tool_name="shell_background_start",
            args_summary={
                "command": command[:160],
                "timeout_ms": timeout_ms,
                "workdir": workdir,
                "tty": tty,
            },
            action=_action,
        )

    @agent.tool(description=_LIST_DESCRIPTION)
    async def shell_background_list(ctx: ToolContext) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_background_terminal_manager(ctx)
            items: list[JsonValue] = [
                cast(JsonValue, _record_payload(record))
                for record in manager.list_for_run(ctx.deps.run_id)
            ]
            return ToolResultProjection(
                visible_data=cast(JsonValue, {"items": items}),
                internal_data=cast(JsonValue, {"items": items}),
            )

        return await execute_tool(
            ctx,
            tool_name="shell_background_list",
            args_summary={},
            action=_action,
        )

    @agent.tool(description=_READ_DESCRIPTION)
    async def shell_background_read(
        ctx: ToolContext,
        terminal_id: str,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_background_terminal_manager(ctx)
            record = manager.get_for_run(
                run_id=ctx.deps.run_id,
                terminal_id=terminal_id,
            )
            return _project_terminal_record(record)

        return await execute_tool(
            ctx,
            tool_name="shell_background_read",
            args_summary={"terminal_id": terminal_id},
            action=_action,
        )

    @agent.tool(description=_WAIT_DESCRIPTION)
    async def shell_background_wait(
        ctx: ToolContext,
        terminal_id: str,
        wait_ms: int = 1000,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_background_terminal_manager(ctx)
            record, completed = await manager.wait_for_run(
                run_id=ctx.deps.run_id,
                terminal_id=terminal_id,
                wait_ms=wait_ms,
            )
            projection = _project_terminal_record(record)
            visible = dict(_visible_dict(projection))
            visible["completed"] = completed
            internal = dict(_internal_dict(projection))
            internal["completed"] = completed
            return ToolResultProjection(visible_data=visible, internal_data=internal)

        return await execute_tool(
            ctx,
            tool_name="shell_background_wait",
            args_summary={"terminal_id": terminal_id, "wait_ms": wait_ms},
            action=_action,
        )

    @agent.tool(description=_WRITE_DESCRIPTION)
    async def shell_background_write(
        ctx: ToolContext,
        terminal_id: str,
        chars: str = "",
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_background_terminal_manager(ctx)
            record = await manager.write_for_run(
                run_id=ctx.deps.run_id,
                terminal_id=terminal_id,
                chars=chars,
            )
            projection = _project_terminal_record(record)
            visible = dict(_visible_dict(projection))
            visible["wrote_chars"] = len(chars)
            internal = dict(_internal_dict(projection))
            internal["wrote_chars"] = len(chars)
            return ToolResultProjection(visible_data=visible, internal_data=internal)

        return await execute_tool(
            ctx,
            tool_name="shell_background_write",
            args_summary={"terminal_id": terminal_id, "chars_length": len(chars)},
            action=_action,
        )

    @agent.tool(description=_RESIZE_DESCRIPTION)
    async def shell_background_resize(
        ctx: ToolContext,
        terminal_id: str,
        columns: int,
        rows: int,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_background_terminal_manager(ctx)
            record = await manager.resize_for_run(
                run_id=ctx.deps.run_id,
                terminal_id=terminal_id,
                columns=columns,
                rows=rows,
            )
            projection = _project_terminal_record(record)
            visible = dict(_visible_dict(projection))
            visible["columns"] = columns
            visible["rows"] = rows
            internal = dict(_internal_dict(projection))
            internal["columns"] = columns
            internal["rows"] = rows
            return ToolResultProjection(visible_data=visible, internal_data=internal)

        return await execute_tool(
            ctx,
            tool_name="shell_background_resize",
            args_summary={
                "terminal_id": terminal_id,
                "columns": columns,
                "rows": rows,
            },
            action=_action,
        )

    @agent.tool(description=_STOP_DESCRIPTION)
    async def shell_background_stop(
        ctx: ToolContext,
        terminal_id: str,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            manager = _require_background_terminal_manager(ctx)
            record = await manager.stop_for_run(
                run_id=ctx.deps.run_id,
                terminal_id=terminal_id,
            )
            return _project_terminal_record(record)

        return await execute_tool(
            ctx,
            tool_name="shell_background_stop",
            args_summary={"terminal_id": terminal_id},
            action=_action,
        )


def _require_background_terminal_manager(ctx: ToolContext):
    manager = ctx.deps.background_terminal_manager
    if manager is None:
        raise RuntimeError("Background terminal manager is not configured")
    return manager


def _resolve_cwd(ctx: ToolContext, workdir: str | None):
    if workdir:
        cwd = ctx.deps.workspace.resolve_workdir(workdir)
    else:
        cwd = ctx.deps.workspace.resolve_workdir()
    if cwd == ctx.deps.workspace.tmp_root and not cwd.exists():
        cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def _record_payload(record: BackgroundTerminalRecord) -> dict[str, JsonValue]:
    return {
        "terminal_id": record.terminal_id,
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
        "stdout_tail": list(record.stdout_tail),
        "stderr_tail": list(record.stderr_tail),
        "log_path": record.log_path,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at is not None else None
        ),
    }


def _project_terminal_record(record: BackgroundTerminalRecord) -> ToolResultProjection:
    payload = _record_payload(record)
    return ToolResultProjection(
        visible_data={"terminal": payload},
        internal_data={"terminal": payload},
    )


def _visible_dict(projection: ToolResultProjection) -> dict[str, JsonValue]:
    visible = projection.visible_data
    if isinstance(visible, dict):
        return {str(key): value for key, value in visible.items()}
    return {}


def _internal_dict(projection: ToolResultProjection) -> dict[str, JsonValue]:
    internal = projection.internal_data
    if isinstance(internal, dict):
        return {str(key): value for key, value in internal.items()}
    return {}
