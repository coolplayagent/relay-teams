# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.metrics.definitions import (
    GATEWAY_OPERATION_DURATION_MS,
    GATEWAY_OPERATION_FAILURES,
    GATEWAY_OPERATIONS,
)
from relay_teams.metrics.models import MetricTagSet
from relay_teams.metrics.recorder import MetricRecorder


def record_gateway_operation(
    recorder: MetricRecorder,
    *,
    workspace_id: str = "",
    session_id: str = "",
    run_id: str = "",
    instance_id: str = "",
    role_id: str = "",
    gateway_channel: str,
    gateway_operation: str,
    gateway_phase: str,
    gateway_transport: str,
    status: str,
    cold_start: bool,
    duration_ms: int,
) -> None:
    tags = MetricTagSet(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        instance_id=instance_id,
        role_id=role_id,
        gateway_channel=gateway_channel,
        gateway_operation=gateway_operation,
        gateway_phase=gateway_phase,
        gateway_transport=gateway_transport,
        gateway_cold_start="true" if cold_start else "false",
        status=status,
    )
    recorder.emit(definition_name=GATEWAY_OPERATIONS.name, value=1, tags=tags)
    recorder.emit(
        definition_name=GATEWAY_OPERATION_DURATION_MS.name,
        value=duration_ms,
        tags=tags,
    )
    if _is_failure_status(status):
        recorder.emit(
            definition_name=GATEWAY_OPERATION_FAILURES.name,
            value=1,
            tags=tags,
        )


def _is_failure_status(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {
        "busy",
        "failed",
        "internal_error",
        "protocol_error",
    }
