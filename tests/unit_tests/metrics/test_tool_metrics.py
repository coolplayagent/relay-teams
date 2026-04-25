# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.metrics import DEFAULT_DEFINITIONS, MetricEvent, MetricRecorder
from relay_teams.metrics.registry import MetricRegistry
from relay_teams.metrics.adapters.tool_metrics import record_tool_execution


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[MetricEvent] = []

    def record(self, event: MetricEvent) -> None:
        self.events.append(event)


@pytest.mark.parametrize(
    "tool_name",
    ("list_skills", "load_skill", "list_skill_roles", "activate_skill_roles"),
)
def test_skill_tools_are_recorded_as_skill_source(tool_name: str) -> None:
    sink = _CapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(sink,),
    )

    record_tool_execution(
        recorder,
        mcp_registry=McpRegistry(),
        workspace_id="workspace-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="instance-1",
        role_id="MainAgent",
        tool_name=tool_name,
        duration_ms=12,
        success=True,
    )

    assert {event.definition_name for event in sink.events} == {
        "relay_teams.skill.calls",
        "relay_teams.tool.calls",
        "relay_teams.tool.duration_ms",
    }
    assert {event.tags.tool_source for event in sink.events} == {"skill"}
