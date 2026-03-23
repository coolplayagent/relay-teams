# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections import defaultdict

from agent_teams.metrics.definitions import (
    LLM_CACHED_INPUT_TOKENS,
    LLM_INPUT_TOKENS,
    LLM_OUTPUT_TOKENS,
    MCP_CALLS,
    SESSION_STEPS,
    SKILL_CALLS,
    TOOL_CALLS,
    TOOL_DURATION_MS,
    TOOL_FAILURES,
)
from agent_teams.metrics.models import (
    MetricScope,
    MetricTagSet,
    MetricsScopeSelector,
    ObservabilityBreakdown,
    ObservabilityBreakdownRow,
    ObservabilityKpiSet,
    ObservabilityOverview,
    ObservabilityTrendPoint,
)
from agent_teams.metrics.stores.sqlite import (
    MetricPointRecord,
    SqliteMetricAggregateStore,
)


class MetricsQueryService:
    def __init__(self, *, store: SqliteMetricAggregateStore) -> None:
        self._store = store

    def get_overview(self, selector: MetricsScopeSelector) -> ObservabilityOverview:
        scope_id = _resolve_scope_id(selector)
        rows = self._store.query_points(
            scope=selector.scope,
            scope_id=scope_id,
            time_window_minutes=selector.time_window_minutes,
        )
        totals = _sum_metric_values(rows)
        tool_calls = totals[TOOL_CALLS.name]
        tool_failures = totals[TOOL_FAILURES.name]
        tool_duration_ms = totals[TOOL_DURATION_MS.name]
        input_tokens = totals[LLM_INPUT_TOKENS.name]
        cached_input_tokens = totals[LLM_CACHED_INPUT_TOKENS.name]
        output_tokens = totals[LLM_OUTPUT_TOKENS.name]
        trends = _build_trends(rows)
        return ObservabilityOverview(
            scope=selector.scope,
            scope_id=scope_id if selector.scope != MetricScope.GLOBAL else "",
            time_window_minutes=selector.time_window_minutes,
            updated_at=self._store.latest_recorded_at(
                scope=selector.scope,
                scope_id=scope_id,
            ),
            kpis=ObservabilityKpiSet(
                steps=totals[SESSION_STEPS.name],
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                cached_token_ratio=(
                    cached_input_tokens / input_tokens if input_tokens > 0 else 0
                ),
                tool_calls=tool_calls,
                tool_success_rate=(
                    (tool_calls - tool_failures) / tool_calls if tool_calls > 0 else 0
                ),
                tool_avg_duration_ms=(
                    tool_duration_ms / tool_calls if tool_calls > 0 else 0
                ),
                skill_calls=totals[SKILL_CALLS.name],
                mcp_calls=totals[MCP_CALLS.name],
            ),
            trends=trends,
        )

    def get_breakdowns(self, selector: MetricsScopeSelector) -> ObservabilityBreakdown:
        scope_id = _resolve_scope_id(selector)
        rows = self._store.query_points(
            scope=selector.scope,
            scope_id=scope_id,
            time_window_minutes=selector.time_window_minutes,
        )
        grouped: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
            lambda: {"calls": 0.0, "failures": 0.0, "duration_ms": 0.0}
        )
        for row in rows:
            tags = _parse_tags(row.tags_json)
            tool_name = tags.tool_name
            if not tool_name:
                continue
            key = (tool_name, tags.tool_source, tags.mcp_server)
            if row.metric_name == TOOL_CALLS.name:
                grouped[key]["calls"] += row.value
            elif row.metric_name == TOOL_FAILURES.name:
                grouped[key]["failures"] += row.value
            elif row.metric_name == TOOL_DURATION_MS.name:
                grouped[key]["duration_ms"] += row.value
        ordered_rows = sorted(
            (
                ObservabilityBreakdownRow(
                    tool_name=tool_name,
                    tool_source=tool_source,
                    mcp_server=mcp_server,
                    calls=values["calls"],
                    failures=values["failures"],
                    success_rate=(
                        (values["calls"] - values["failures"]) / values["calls"]
                        if values["calls"] > 0
                        else 0
                    ),
                    avg_duration_ms=(
                        values["duration_ms"] / values["calls"]
                        if values["calls"] > 0
                        else 0
                    ),
                )
                for (tool_name, tool_source, mcp_server), values in grouped.items()
            ),
            key=lambda item: (-item.calls, item.tool_name),
        )
        return ObservabilityBreakdown(
            scope=selector.scope,
            scope_id=scope_id if selector.scope != MetricScope.GLOBAL else "",
            time_window_minutes=selector.time_window_minutes,
            updated_at=self._store.latest_recorded_at(
                scope=selector.scope,
                scope_id=scope_id,
            ),
            rows=tuple(ordered_rows),
        )


def _resolve_scope_id(selector: MetricsScopeSelector) -> str:
    if selector.scope == MetricScope.GLOBAL:
        return "global"
    return selector.scope_id


def _sum_metric_values(rows: tuple[MetricPointRecord, ...]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        totals[row.metric_name] += row.value
    return totals


def _build_trends(
    rows: tuple[MetricPointRecord, ...],
) -> tuple[ObservabilityTrendPoint, ...]:
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        bucket_key = row.bucket_start.isoformat()
        buckets[bucket_key][row.metric_name] += row.value
    return tuple(
        ObservabilityTrendPoint(
            bucket_start=bucket_start,
            steps=values[SESSION_STEPS.name],
            input_tokens=values[LLM_INPUT_TOKENS.name],
            output_tokens=values[LLM_OUTPUT_TOKENS.name],
            tool_calls=values[TOOL_CALLS.name],
        )
        for bucket_start, values in sorted(buckets.items())
    )


def _parse_tags(raw_tags: str) -> MetricTagSet:
    parsed = json.loads(raw_tags)
    if not isinstance(parsed, dict):
        return MetricTagSet()
    normalized = {
        str(key): str(value) for key, value in parsed.items() if isinstance(key, str)
    }
    return MetricTagSet(**normalized)
