# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable

from pydantic import JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.tools.runtime.context import ToolContext

LOGGER = get_logger(__name__)
TOOL_STEP_CONCURRENCY_ENV = "RELAY_TEAMS_TOOL_STEP_CONCURRENCY"
TOOL_PENDING_WARNING_THRESHOLD_ENV = "RELAY_TEAMS_TOOL_PENDING_WARNING_THRESHOLD"
PER_RUN_TOOL_ACTION_CONCURRENCY = 8


def _resolve_positive_int_env(name: str, default: int) -> int:  # pragma: no cover
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.runtime.invalid_env",
            message="Ignoring invalid tool runtime environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    if value < 1:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.runtime.invalid_env",
            message="Ignoring non-positive tool runtime environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    return value


GLOBAL_TOOL_ACTION_CONCURRENCY = _resolve_positive_int_env(
    TOOL_STEP_CONCURRENCY_ENV,
    16,
)
TOOL_PENDING_WARNING_THRESHOLD = _resolve_positive_int_env(
    TOOL_PENDING_WARNING_THRESHOLD_ENV,
    200,
)
GLOBAL_TOOL_ACTION_SEMAPHORE = asyncio.Semaphore(GLOBAL_TOOL_ACTION_CONCURRENCY)
_RUN_TOOL_ACTION_GATES_LOCK = threading.Lock()
RUN_TOOL_ACTION_GATES: dict[str, _RunToolActionGate] = {}
_TOOL_ACTION_PENDING_LOCK = threading.Lock()
_GLOBAL_PENDING_TOOL_ACTIONS = 0
_PENDING_TOOL_ACTIONS_BY_RUN: dict[str, int] = {}


class _RunToolActionGate:
    def __init__(self) -> None:
        self.semaphore = asyncio.Semaphore(PER_RUN_TOOL_ACTION_CONCURRENCY)
        self.ref_count = 0


class _PendingToolActionWarningState:  # pragma: no cover
    def __init__(self) -> None:
        self.last_warning_at = 0.0

    def should_warn(self, *, pending_total: int, now: float) -> bool:
        if pending_total <= TOOL_PENDING_WARNING_THRESHOLD:
            return False
        if now - self.last_warning_at < 5.0:
            return False
        self.last_warning_at = now
        return True


_PENDING_TOOL_ACTION_WARNING_STATE = _PendingToolActionWarningState()


async def invoke_with_tool_action_capacity(
    *,
    ctx: ToolContext,
    runtime_meta: dict[str, JsonValue],
    action_factory: Callable[[], Awaitable[object]],
    hold_action_capacity: bool = True,
) -> object:
    if not hold_action_capacity:
        runtime_meta["action_queue_wait_ms"] = 0
        runtime_meta["tool_action_capacity_held"] = False
        action_started = time.perf_counter()
        try:
            return await action_factory()
        finally:
            runtime_meta["action_duration_ms"] = int(
                (time.perf_counter() - action_started) * 1000
            )
    run_gate = _retain_run_tool_action_gate(ctx.deps.run_id)
    pending_total, pending_for_run, should_warn = _retain_pending_tool_action(
        ctx.deps.run_id
    )
    if should_warn:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.action.pending_threshold_exceeded",
            message="Pending tool action queue exceeded the warning threshold",
            payload={
                "run_id": ctx.deps.run_id,
                "session_id": ctx.deps.session_id,
                "tool_call_id": ctx.tool_call_id,
                "pending_total": pending_total,
                "pending_for_run": pending_for_run,
                "threshold": TOOL_PENDING_WARNING_THRESHOLD,
            },
        )
    queued_at = time.perf_counter()
    try:
        async with run_gate.semaphore:
            async with GLOBAL_TOOL_ACTION_SEMAPHORE:
                wait_ms = int((time.perf_counter() - queued_at) * 1000)
                runtime_meta["action_queue_wait_ms"] = wait_ms
                runtime_meta["tool_action_capacity_held"] = True
                if wait_ms >= 250:
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        event="tool.action.queue_wait",
                        message="Tool action waited for execution capacity",
                        duration_ms=wait_ms,
                        payload={
                            "run_id": ctx.deps.run_id,
                            "session_id": ctx.deps.session_id,
                            "tool_call_id": ctx.tool_call_id,
                        },
                    )
                action_started = time.perf_counter()
                try:
                    return await action_factory()
                finally:
                    runtime_meta["action_duration_ms"] = int(
                        (time.perf_counter() - action_started) * 1000
                    )
    finally:
        _release_pending_tool_action(ctx.deps.run_id)
        _release_run_tool_action_gate(ctx.deps.run_id, run_gate)


def _retain_pending_tool_action(run_id: str) -> tuple[int, int, bool]:
    global _GLOBAL_PENDING_TOOL_ACTIONS
    now = time.monotonic()
    with _TOOL_ACTION_PENDING_LOCK:
        _GLOBAL_PENDING_TOOL_ACTIONS += 1
        pending_for_run = _PENDING_TOOL_ACTIONS_BY_RUN.get(run_id, 0) + 1
        _PENDING_TOOL_ACTIONS_BY_RUN[run_id] = pending_for_run
        should_warn = _PENDING_TOOL_ACTION_WARNING_STATE.should_warn(
            pending_total=_GLOBAL_PENDING_TOOL_ACTIONS,
            now=now,
        )
        return _GLOBAL_PENDING_TOOL_ACTIONS, pending_for_run, should_warn


def _release_pending_tool_action(run_id: str) -> None:
    global _GLOBAL_PENDING_TOOL_ACTIONS
    with _TOOL_ACTION_PENDING_LOCK:
        _GLOBAL_PENDING_TOOL_ACTIONS = max(0, _GLOBAL_PENDING_TOOL_ACTIONS - 1)
        pending_for_run = max(0, _PENDING_TOOL_ACTIONS_BY_RUN.get(run_id, 0) - 1)
        if pending_for_run == 0:
            _PENDING_TOOL_ACTIONS_BY_RUN.pop(run_id, None)
            return
        _PENDING_TOOL_ACTIONS_BY_RUN[run_id] = pending_for_run


def _retain_run_tool_action_gate(run_id: str) -> _RunToolActionGate:
    with _RUN_TOOL_ACTION_GATES_LOCK:
        gate = RUN_TOOL_ACTION_GATES.get(run_id)
        if gate is None:
            gate = _RunToolActionGate()
            RUN_TOOL_ACTION_GATES[run_id] = gate
        gate.ref_count += 1
        return gate


def _release_run_tool_action_gate(run_id: str, gate: _RunToolActionGate) -> None:
    with _RUN_TOOL_ACTION_GATES_LOCK:
        current = RUN_TOOL_ACTION_GATES.get(run_id)
        if current is not gate:
            return
        gate.ref_count = max(0, gate.ref_count - 1)
        if gate.ref_count == 0:
            del RUN_TOOL_ACTION_GATES[run_id]
