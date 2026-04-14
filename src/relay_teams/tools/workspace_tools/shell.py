# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

from relay_teams.computer import ComputerActionRisk
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolApprovalRequest,
    ToolContext,
    ToolDeps,
    ToolExecutionError,
    execute_tool,
)
from relay_teams.tools.workspace_tools.background_task_tool_support import (
    project_background_task_tool_result,
    require_background_task_service,
)
from relay_teams.tools.workspace_tools.command_canonicalization import (
    canonicalize_shell_command,
)
from relay_teams.tools.workspace_tools.shell_approval_repo import ShellApprovalScope
from relay_teams.tools.workspace_tools.shell_policy import (
    ShellPolicyDecision,
    validate_shell_command,
)

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
        blocked_error: ToolExecutionError | None = None
        shell_policy: ShellPolicyDecision | None = None
        cwd: Path | None = None
        try:
            shell_policy, cwd = prepare_shell_execution(
                ctx,
                command=command,
                workdir=workdir,
            )
        except ToolExecutionError as exc:
            blocked_error = exc
        approval_request = (
            build_shell_blocked_approval_request()
            if blocked_error is not None
            else build_shell_approval_request(
                ctx,
                shell_policy=_require_shell_policy(shell_policy),
                command=command,
                workdir=workdir,
                tty=tty,
                background=background,
            )
        )

        async def _action():
            if blocked_error is not None:
                raise blocked_error
            if cwd is None:
                raise RuntimeError("shell execution cwd was not prepared")
            service = require_background_task_service(ctx)
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
                env={**ctx.deps.hook_env, CURRENT_ROLE_ENV_KEY: ctx.deps.role_id},
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


def build_shell_approval_request(
    ctx: ToolContext,
    *,
    shell_policy: ShellPolicyDecision,
    command: str,
    workdir: str | None,
    tty: bool,
    background: bool,
) -> ToolApprovalRequest:
    cache_key = build_shell_cache_key(
        command,
        workdir=workdir,
        tty=tty,
        background=background,
    )
    metadata = build_shell_approval_metadata(ctx, shell_policy=shell_policy)
    repo = ctx.deps.shell_approval_repo
    if repo is not None and not ctx.deps.tool_approval_policy.yolo:
        workspace_key = str(metadata["workspace_key"])
        runtime_family = shell_policy.runtime_family
        normalized_command = shell_policy.normalized_command
        prefix_candidates = shell_policy.prefix_candidates
        if repo.has_exact_grant(
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            normalized_command=normalized_command,
        ) or repo.has_prefix_grants(
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            prefix_candidates=prefix_candidates,
        ):
            return ToolApprovalRequest(
                cache_key=cache_key,
                risk_level=ComputerActionRisk.SAFE,
                source="shell_saved_permission",
                target_summary=", ".join(prefix_candidates)[:200],
                metadata=metadata,
            )
    return ToolApprovalRequest(
        cache_key=cache_key,
        source="shell",
        target_summary=", ".join(shell_policy.prefix_candidates)[:200],
        metadata=metadata,
    )


def build_shell_blocked_approval_request() -> ToolApprovalRequest:
    return ToolApprovalRequest(
        risk_level=ComputerActionRisk.SAFE,
        source="shell_local_policy",
    )


def build_shell_approval_metadata(
    ctx: ToolContext,
    *,
    shell_policy: ShellPolicyDecision,
) -> dict[str, JsonValue]:
    return {
        "workspace_key": str(ctx.deps.workspace.execution_root.resolve()),
        "runtime_family": shell_policy.runtime_family.value,
        "normalized_command": shell_policy.normalized_command,
        "prefix_candidates": cast(
            list[JsonValue], list(shell_policy.prefix_candidates)
        ),
        "approval_scope_values": [
            ShellApprovalScope.EXACT.value,
            ShellApprovalScope.PREFIX.value,
        ],
    }


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


def prepare_shell_execution(
    ctx: ToolContext,
    *,
    command: str,
    workdir: str | None,
) -> tuple[ShellPolicyDecision, Path]:
    try:
        cwd = resolve_cwd(ctx, workdir)
        shell_policy = validate_shell_command(
            command,
            yolo=ctx.deps.tool_approval_policy.yolo,
            effective_cwd=cwd,
        )
    except ValueError as exc:
        raise ToolExecutionError(
            error_type="tool_blocked",
            message=str(exc),
            retryable=False,
        ) from exc
    return shell_policy, cwd


def _require_shell_policy(
    shell_policy: ShellPolicyDecision | None,
) -> ShellPolicyDecision:
    if shell_policy is None:
        raise RuntimeError("shell execution preparation produced incomplete state")
    return shell_policy
