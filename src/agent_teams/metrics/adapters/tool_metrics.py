# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.metrics.definitions import (
    MCP_CALLS,
    SKILL_CALLS,
    TOOL_CALLS,
    TOOL_DURATION_MS,
    TOOL_FAILURES,
)
from agent_teams.metrics.models import MetricTagSet
from agent_teams.metrics.recorder import MetricRecorder


class ToolSource(str, Enum):
    LOCAL = "local"
    SKILL = "skill"
    MCP = "mcp"


SKILL_TOOL_NAMES = frozenset({"load_skill"})


def record_tool_execution(
    recorder: MetricRecorder,
    *,
    mcp_registry: McpRegistry,
    workspace_id: str,
    session_id: str,
    run_id: str,
    instance_id: str,
    role_id: str,
    tool_name: str,
    duration_ms: int,
    success: bool,
) -> None:
    source, mcp_server = _resolve_tool_source(
        tool_name=tool_name, mcp_registry=mcp_registry
    )
    tags = MetricTagSet(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        instance_id=instance_id,
        role_id=role_id,
        tool_name=tool_name,
        tool_source=source.value,
        mcp_server=mcp_server,
        status="success" if success else "failure",
    )
    recorder.emit(definition_name=TOOL_CALLS.name, value=1, tags=tags)
    recorder.emit(definition_name=TOOL_DURATION_MS.name, value=duration_ms, tags=tags)
    if not success:
        recorder.emit(definition_name=TOOL_FAILURES.name, value=1, tags=tags)
    if source == ToolSource.SKILL:
        recorder.emit(definition_name=SKILL_CALLS.name, value=1, tags=tags)
    if source == ToolSource.MCP:
        recorder.emit(definition_name=MCP_CALLS.name, value=1, tags=tags)


def _resolve_tool_source(
    *,
    tool_name: str,
    mcp_registry: McpRegistry,
) -> tuple[ToolSource, str]:
    if tool_name in SKILL_TOOL_NAMES:
        return ToolSource.SKILL, ""
    for server_name in mcp_registry.list_names():
        prefix = f"{server_name}_"
        if tool_name.startswith(prefix):
            return ToolSource.MCP, server_name
    return ToolSource.LOCAL, ""
