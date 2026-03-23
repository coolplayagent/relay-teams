# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.metrics.definitions import SESSION_STEPS
from agent_teams.metrics.models import MetricTagSet
from agent_teams.metrics.recorder import MetricRecorder


def record_session_step(
    recorder: MetricRecorder,
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    instance_id: str,
    role_id: str,
) -> None:
    recorder.emit(
        definition_name=SESSION_STEPS.name,
        value=1,
        tags=MetricTagSet(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            instance_id=instance_id,
            role_id=role_id,
        ),
    )
