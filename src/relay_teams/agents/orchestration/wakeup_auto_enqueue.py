# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime, timezone

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import (
    TaskStatus,
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)


async def enqueue_dependency_wakeups(
    *,
    completed_task_id: str,
    completed_task_envelope: TaskEnvelope,
    task_repo: TaskRepository,
    wakeup_repo: AgentWakeupRepository,
) -> int:
    """Enqueue ``DEPENDENCY_RESOLVED`` wakes for every task whose
    ``depends_on_task_ids`` includes *completed_task_id*."""

    trace_id = completed_task_envelope.trace_id or ""
    session_id = completed_task_envelope.session_id or ""
    if not trace_id:
        return 0

    try:
        all_records = await task_repo.list_by_trace_async(trace_id)
    except (OSError, ValueError, RuntimeError):
        log_event(
            LOGGER,
            logging.WARNING,
            event="wakeup.dependency.lookup_failed",
            message="Failed to list tasks by trace for dependency wakeup",
            payload={"completed_task_id": completed_task_id, "trace_id": trace_id},
        )
        return 0

    enqueued = 0
    now = datetime.now(tz=timezone.utc)
    for record in all_records:
        if record.envelope.task_id == completed_task_id:
            continue
        if completed_task_id not in record.envelope.depends_on_task_ids:
            continue
        downstream_id = record.envelope.task_id
        coalesce_key = f"{downstream_id}:dep:{completed_task_id}"
        entry = AgentWakeupEntry(
            wakeup_id=f"wk_dep_{downstream_id}_{int(now.timestamp())}",
            task_id=downstream_id,
            trace_id=trace_id,
            session_id=session_id,
            coalesce_key=coalesce_key,
            timeout_action=TaskTimeoutAction.RETRY,
            timeout_seconds=0.0,
            attempt=1,
            max_attempts=3,
            status=WakeupStatus.PENDING,
            enqueued_at=now,
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
            target_role=record.envelope.role_id or "",
            source_event_type="task_completed",
            source_trigger_id=completed_task_id,
        )
        try:
            inserted = await wakeup_repo.coalesce_and_enqueue_async(entry)
            if inserted:
                enqueued += 1
                log_event(
                    LOGGER,
                    logging.INFO,
                    event="wakeup.dependency.enqueued",
                    message="Dependency-resolved wakeup enqueued",
                    payload={
                        "downstream_task_id": downstream_id,
                        "completed_task_id": completed_task_id,
                    },
                )
        except (OSError, ValueError, RuntimeError):
            log_event(
                LOGGER,
                logging.WARNING,
                event="wakeup.dependency.enqueue_failed",
                message="Failed to enqueue dependency wakeup",
                payload={
                    "downstream_task_id": downstream_id,
                    "completed_task_id": completed_task_id,
                },
            )

    return enqueued


async def enqueue_blocker_resolved_wakeups(
    *,
    completed_task_id: str,
    completed_task_envelope: TaskEnvelope,
    task_repo: TaskRepository,
    wakeup_repo: AgentWakeupRepository,
) -> int:
    """Enqueue ``DEPENDENCY_RESOLVED`` wakes for tasks blocked by
    *completed_task_id* (``blocked_by_task_ids``)."""

    trace_id = completed_task_envelope.trace_id or ""
    session_id = completed_task_envelope.session_id or ""
    if not trace_id:
        return 0

    try:
        all_records = await task_repo.list_by_trace_async(trace_id)
    except (OSError, ValueError, RuntimeError):
        log_event(
            LOGGER,
            logging.WARNING,
            event="wakeup.blocker.lookup_failed",
            message="Failed to list tasks by trace for blocker wakeup",
            payload={"completed_task_id": completed_task_id, "trace_id": trace_id},
        )
        return 0

    enqueued = 0
    now = datetime.now(tz=timezone.utc)
    for record in all_records:
        if record.envelope.task_id == completed_task_id:
            continue
        blocked_by = getattr(record.envelope, "blocked_by_task_ids", ())
        if not blocked_by or completed_task_id not in blocked_by:
            continue
        # Check whether *all* blockers are now resolved
        still_blocked = _any_blocker_pending(
            record=record,
            all_records=all_records,
        )
        if still_blocked:
            continue

        downstream_id = record.envelope.task_id
        coalesce_key = f"{downstream_id}:blocker:{completed_task_id}"
        entry = AgentWakeupEntry(
            wakeup_id=f"wk_blk_{downstream_id}_{int(now.timestamp())}",
            task_id=downstream_id,
            trace_id=trace_id,
            session_id=session_id,
            coalesce_key=coalesce_key,
            timeout_action=TaskTimeoutAction.RETRY,
            timeout_seconds=0.0,
            attempt=1,
            max_attempts=3,
            status=WakeupStatus.PENDING,
            enqueued_at=now,
            wake_reason=WakeupReason.DEPENDENCY_RESOLVED,
            target_role=record.envelope.role_id or "",
            source_event_type="blocker_resolved",
            source_trigger_id=completed_task_id,
        )
        try:
            inserted = await wakeup_repo.coalesce_and_enqueue_async(entry)
            if inserted:
                enqueued += 1
                log_event(
                    LOGGER,
                    logging.INFO,
                    event="wakeup.blocker_resolved.enqueued",
                    message="Blocker-resolved wakeup enqueued",
                    payload={
                        "downstream_task_id": downstream_id,
                        "completed_task_id": completed_task_id,
                    },
                )
        except (OSError, ValueError, RuntimeError):
            log_event(
                LOGGER,
                logging.WARNING,
                event="wakeup.blocker.enqueue_failed",
                message="Failed to enqueue blocker-resolved wakeup",
                payload={
                    "downstream_task_id": downstream_id,
                    "completed_task_id": completed_task_id,
                },
            )

    return enqueued


def _any_blocker_pending(
    *,
    record,
    all_records: tuple,
) -> bool:
    """Return ``True`` if at least one blocker of *record* is still not COMPLETED."""
    blocked_by = getattr(record.envelope, "blocked_by_task_ids", ())
    if not blocked_by:
        return False
    for blocker_id in blocked_by:
        for r in all_records:
            if r.envelope.task_id == blocker_id:
                if r.status != TaskStatus.COMPLETED:
                    return True
                break
        else:
            # Blocker task was deleted — treat as resolved
            pass
    return False


async def enqueue_approval_wakeups(
    *,
    task_id: str,
    trace_id: str,
    session_id: str,
    gate_id: str,
    task_repo: TaskRepository,
    wakeup_repo: AgentWakeupRepository,
) -> int:
    """Enqueue ``APPROVAL_PASSED`` wakes for tasks blocked by a
    human gate that was just approved."""

    now = datetime.now(tz=timezone.utc)
    coalesce_key = f"{task_id}:approval:{gate_id}"
    try:
        task_record = await task_repo.get_async(task_id)
    except KeyError:
        return 0

    if task_record.status != TaskStatus.STOPPED:
        return 0

    entry = AgentWakeupEntry(
        wakeup_id=f"wk_approval_{task_id}_{int(now.timestamp())}",
        task_id=task_id,
        trace_id=trace_id,
        session_id=session_id,
        coalesce_key=coalesce_key,
        timeout_action=TaskTimeoutAction.RETRY,
        timeout_seconds=0.0,
        attempt=1,
        max_attempts=3,
        status=WakeupStatus.PENDING,
        enqueued_at=now,
        wake_reason=WakeupReason.APPROVAL_PASSED,
        target_role=task_record.envelope.role_id or "",
        source_event_type="approval_passed",
        source_trigger_id=gate_id,
    )
    try:
        inserted = await wakeup_repo.coalesce_and_enqueue_async(entry)
        if inserted:
            log_event(
                LOGGER,
                logging.INFO,
                event="wakeup.approval.enqueued",
                message="Approval-passed wakeup enqueued",
                payload={"task_id": task_id, "gate_id": gate_id},
            )
            return 1
    except (OSError, ValueError, RuntimeError):
        log_event(
            LOGGER,
            logging.WARNING,
            event="wakeup.approval.enqueue_failed",
            message="Failed to enqueue approval wakeup",
            payload={"task_id": task_id, "gate_id": gate_id},
        )
    return 0
