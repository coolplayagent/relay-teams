# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.metrics import (
    DEFAULT_DEFINITIONS,
    MetricEvent,
    MetricRecorder,
    MetricTagSet,
)
from relay_teams.metrics.adapters.llm_metrics import record_token_usage_async
from relay_teams.metrics.adapters.session_metrics import record_session_step_async
from relay_teams.metrics.registry import MetricRegistry
from relay_teams.metrics.adapters.tool_metrics import (
    record_tool_execution,
    record_tool_execution_async,
)


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[MetricEvent] = []

    def record(self, event: MetricEvent) -> None:
        self.events.append(event)

    async def record_async(self, event: MetricEvent) -> None:
        self.events.append(event)


class _SyncOnlyCapturingSink:
    def __init__(self) -> None:
        self.events: list[MetricEvent] = []

    def record(self, event: MetricEvent) -> None:
        self.events.append(event)


class _FailingSink:
    def record(self, event: MetricEvent) -> None:
        _ = event
        raise RuntimeError("metric sink failed")

    async def record_async(self, event: MetricEvent) -> None:
        _ = event
        raise RuntimeError("metric sink failed")


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


@pytest.mark.asyncio
async def test_async_tool_metrics_record_failure_and_mcp_source() -> None:
    sink = _CapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(sink,),
    )
    registry = McpRegistry(
        specs=(
            McpServerSpec(
                name="filesystem",
                config={"command": "server"},
                server_config={"command": "server"},
                source=McpConfigScope.APP,
            ),
        )
    )

    await record_tool_execution_async(
        recorder,
        mcp_registry=registry,
        workspace_id="workspace-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="instance-1",
        role_id="MainAgent",
        tool_name="filesystem_read_file",
        duration_ms=12,
        success=False,
    )

    assert {event.definition_name for event in sink.events} == {
        "relay_teams.mcp.calls",
        "relay_teams.tool.calls",
        "relay_teams.tool.duration_ms",
        "relay_teams.tool.failures",
    }
    assert {event.tags.tool_source for event in sink.events} == {"mcp"}
    assert {event.tags.mcp_server for event in sink.events} == {"filesystem"}
    assert {event.tags.status for event in sink.events} == {"failure"}


@pytest.mark.asyncio
async def test_async_tool_metrics_record_skill_source() -> None:
    sink = _CapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(sink,),
    )

    await record_tool_execution_async(
        recorder,
        mcp_registry=McpRegistry(),
        workspace_id="workspace-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="instance-1",
        role_id="MainAgent",
        tool_name="load_skill",
        duration_ms=12,
        success=True,
    )

    assert {event.definition_name for event in sink.events} == {
        "relay_teams.skill.calls",
        "relay_teams.tool.calls",
        "relay_teams.tool.duration_ms",
    }
    assert {event.tags.tool_source for event in sink.events} == {"skill"}


@pytest.mark.asyncio
async def test_async_token_usage_records_positive_counts() -> None:
    sink = _CapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(sink,),
    )

    await record_token_usage_async(
        recorder,
        workspace_id="workspace-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="instance-1",
        role_id="MainAgent",
        input_tokens=120,
        cached_input_tokens=48,
        output_tokens=24,
    )

    assert [event.definition_name for event in sink.events] == [
        "relay_teams.llm.input_tokens",
        "relay_teams.llm.cached_input_tokens",
        "relay_teams.llm.output_tokens",
    ]
    assert [event.value for event in sink.events] == [120, 48, 24]


@pytest.mark.asyncio
async def test_async_session_step_uses_recorder_async_path() -> None:
    sink = _CapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(sink,),
    )

    await record_session_step_async(
        recorder,
        workspace_id="workspace-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="instance-1",
        role_id="MainAgent",
    )

    assert [event.definition_name for event in sink.events] == [
        "relay_teams.session.steps"
    ]
    assert sink.events[0].tags.session_id == "session-1"


@pytest.mark.asyncio
async def test_metric_recorder_async_supports_sync_sinks_and_ignores_failures() -> None:
    sink = _SyncOnlyCapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(_FailingSink(), sink),
    )

    await recorder.emit_async(
        definition_name="relay_teams.session.steps",
        value=1,
        tags=_metric_tags(),
    )

    assert [event.definition_name for event in sink.events] == [
        "relay_teams.session.steps"
    ]


def test_metric_recorder_emit_ignores_sink_failures() -> None:
    sink = _SyncOnlyCapturingSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(_FailingSink(), sink),
    )

    recorder.emit(
        definition_name="relay_teams.session.steps",
        value=1,
        tags=_metric_tags(),
    )

    assert [event.definition_name for event in sink.events] == [
        "relay_teams.session.steps"
    ]


def _metric_tags() -> MetricTagSet:
    return MetricTagSet(
        workspace_id="workspace-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="instance-1",
        role_id="MainAgent",
    )
