# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.metrics.models import MetricDefinition, MetricKind

SESSION_STEPS = MetricDefinition(
    name="agent_teams.session.steps",
    kind=MetricKind.COUNTER,
    description="Count of model steps executed for a session or run.",
    unit="steps",
)
LLM_INPUT_TOKENS = MetricDefinition(
    name="agent_teams.llm.input_tokens",
    kind=MetricKind.COUNTER,
    description="Input tokens sent to the model provider.",
    unit="tokens",
)
LLM_CACHED_INPUT_TOKENS = MetricDefinition(
    name="agent_teams.llm.cached_input_tokens",
    kind=MetricKind.COUNTER,
    description="Cached input tokens reported by the model provider.",
    unit="tokens",
)
LLM_OUTPUT_TOKENS = MetricDefinition(
    name="agent_teams.llm.output_tokens",
    kind=MetricKind.COUNTER,
    description="Output tokens reported by the model provider.",
    unit="tokens",
)
TOOL_CALLS = MetricDefinition(
    name="agent_teams.tool.calls",
    kind=MetricKind.COUNTER,
    description="Total tool calls executed by the runtime.",
    unit="calls",
)
TOOL_DURATION_MS = MetricDefinition(
    name="agent_teams.tool.duration_ms",
    kind=MetricKind.HISTOGRAM,
    description="Observed duration of tool calls in milliseconds.",
    unit="ms",
)
TOOL_FAILURES = MetricDefinition(
    name="agent_teams.tool.failures",
    kind=MetricKind.COUNTER,
    description="Count of failed tool calls.",
    unit="calls",
)
SKILL_CALLS = MetricDefinition(
    name="agent_teams.skill.calls",
    kind=MetricKind.COUNTER,
    description="Count of tool calls sourced from skills.",
    unit="calls",
)
MCP_CALLS = MetricDefinition(
    name="agent_teams.mcp.calls",
    kind=MetricKind.COUNTER,
    description="Count of tool calls sourced from MCP servers.",
    unit="calls",
)

DEFAULT_DEFINITIONS = (
    SESSION_STEPS,
    LLM_INPUT_TOKENS,
    LLM_CACHED_INPUT_TOKENS,
    LLM_OUTPUT_TOKENS,
    TOOL_CALLS,
    TOOL_DURATION_MS,
    TOOL_FAILURES,
    SKILL_CALLS,
    MCP_CALLS,
)
