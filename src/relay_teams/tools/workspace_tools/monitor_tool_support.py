# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from pydantic import JsonValue

from relay_teams.monitors import MonitorService, MonitorSubscriptionRecord
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.models import ToolResultProjection


def require_monitor_service(ctx: ToolContext) -> MonitorService:
    service = ctx.deps.monitor_service
    if service is None:
        raise RuntimeError("Monitor service is not configured")
    return service


def project_monitor_tool_result(
    record: MonitorSubscriptionRecord,
) -> ToolResultProjection:
    payload: dict[str, JsonValue] = {"monitor": json.loads(record.model_dump_json())}
    return ToolResultProjection(visible_data=payload, internal_data=payload)
