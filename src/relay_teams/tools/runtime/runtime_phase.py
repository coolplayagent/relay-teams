# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from pydantic import JsonValue

from relay_teams.sessions.runs.run_runtime_repo import RunRuntimePhase
from relay_teams.tools.runtime.context import ToolContext


def _is_root_or_coordinator_context(ctx: ToolContext) -> bool:
    return bool(
        getattr(ctx.deps, "is_root_task_context", False)
    ) or ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id)


def _running_runtime_phase(ctx: ToolContext) -> RunRuntimePhase:
    if _is_root_or_coordinator_context(ctx):
        return RunRuntimePhase.COORDINATOR_RUNNING
    return RunRuntimePhase.SUBAGENT_RUNNING


def _active_subagent_instance_id(ctx: ToolContext) -> str | None:
    if _is_root_or_coordinator_context(ctx):
        return None
    return ctx.deps.instance_id


def _finalize_tool_timing_meta(
    *,
    runtime_meta: dict[str, JsonValue],
    started: float,
) -> int:
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    action_duration_ms = runtime_meta.get("action_duration_ms")
    resolved_action_duration_ms = (
        action_duration_ms if type(action_duration_ms) is int else elapsed_ms
    )
    runtime_meta["duration_ms"] = resolved_action_duration_ms
    runtime_meta["total_tool_runtime_ms"] = elapsed_ms
    runtime_meta["tool_batch_wall_ms"] = elapsed_ms
    runtime_meta["tool_framework_wait_ms"] = max(
        0,
        elapsed_ms
        - int(resolved_action_duration_ms)
        - _int_meta(runtime_meta, "action_queue_wait_ms"),
    )
    return elapsed_ms


def _int_meta(runtime_meta: dict[str, JsonValue], key: str) -> int:
    value = runtime_meta.get(key)
    if type(value) is int:
        return value
    return 0
