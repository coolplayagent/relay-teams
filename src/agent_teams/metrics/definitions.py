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
RETRIEVAL_SEARCHES = MetricDefinition(
    name="agent_teams.retrieval.searches",
    kind=MetricKind.COUNTER,
    description="Count of retrieval search operations.",
    unit="searches",
)
RETRIEVAL_SEARCH_DURATION_MS = MetricDefinition(
    name="agent_teams.retrieval.search_duration_ms",
    kind=MetricKind.HISTOGRAM,
    description="Observed duration of retrieval search operations in milliseconds.",
    unit="ms",
)
RETRIEVAL_SEARCH_FAILURES = MetricDefinition(
    name="agent_teams.retrieval.search_failures",
    kind=MetricKind.COUNTER,
    description="Count of failed retrieval search operations.",
    unit="searches",
)
RETRIEVAL_REBUILDS = MetricDefinition(
    name="agent_teams.retrieval.rebuilds",
    kind=MetricKind.COUNTER,
    description="Count of retrieval index rebuild operations.",
    unit="rebuilds",
)
RETRIEVAL_REBUILD_DURATION_MS = MetricDefinition(
    name="agent_teams.retrieval.rebuild_duration_ms",
    kind=MetricKind.HISTOGRAM,
    description="Observed duration of retrieval rebuild operations in milliseconds.",
    unit="ms",
)
RETRIEVAL_DOCUMENT_COUNT = MetricDefinition(
    name="agent_teams.retrieval.document_count",
    kind=MetricKind.GAUGE,
    description="Current document count tracked by a retrieval scope.",
    unit="documents",
)
GATEWAY_OPERATIONS = MetricDefinition(
    name="agent_teams.gateway.operations",
    kind=MetricKind.COUNTER,
    description="Count of gateway ACP and MCP operations observed by the runtime.",
    unit="calls",
)
GATEWAY_OPERATION_DURATION_MS = MetricDefinition(
    name="agent_teams.gateway.operation_duration_ms",
    kind=MetricKind.HISTOGRAM,
    description="Observed duration of gateway ACP and MCP operations in milliseconds.",
    unit="ms",
)
GATEWAY_OPERATION_FAILURES = MetricDefinition(
    name="agent_teams.gateway.operation_failures",
    kind=MetricKind.COUNTER,
    description="Count of failed gateway ACP and MCP operations.",
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
    RETRIEVAL_SEARCHES,
    RETRIEVAL_SEARCH_DURATION_MS,
    RETRIEVAL_SEARCH_FAILURES,
    RETRIEVAL_REBUILDS,
    RETRIEVAL_REBUILD_DURATION_MS,
    RETRIEVAL_DOCUMENT_COUNT,
    GATEWAY_OPERATIONS,
    GATEWAY_OPERATION_DURATION_MS,
    GATEWAY_OPERATION_FAILURES,
)
