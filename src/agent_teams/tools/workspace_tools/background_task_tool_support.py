# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.sessions.runs.background_tasks import BackgroundTaskService
from agent_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
from agent_teams.sessions.runs.background_tasks.projection import (
    build_background_task_result_payload,
)
from agent_teams.tools.runtime import ToolContext, ToolResultProjection


def require_background_task_service(ctx: ToolContext) -> BackgroundTaskService:
    service = ctx.deps.background_task_service
    if service is None:
        raise RuntimeError("Background task service is not configured")
    return service


def project_background_task_tool_result(
    record: BackgroundTaskRecord,
    *,
    completed: bool,
    include_task_id: bool,
) -> ToolResultProjection:
    payload = build_background_task_result_payload(
        record,
        completed=completed,
        include_task_id=include_task_id,
    )
    return ToolResultProjection(visible_data=payload, internal_data=payload)
