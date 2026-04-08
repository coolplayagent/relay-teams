# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable

from relay_teams.metrics.definitions import (
    GATEWAY_OPERATION_DURATION_MS,
    GATEWAY_OPERATION_FAILURES,
    GATEWAY_OPERATIONS,
    LLM_CACHED_INPUT_TOKENS,
    LLM_INPUT_TOKENS,
    LLM_OUTPUT_TOKENS,
    MCP_CALLS,
    RETRIEVAL_DOCUMENT_COUNT,
    RETRIEVAL_SEARCH_DURATION_MS,
    RETRIEVAL_SEARCH_FAILURES,
    RETRIEVAL_SEARCHES,
    SESSION_STEPS,
    SKILL_CALLS,
    TOOL_CALLS,
    TOOL_DURATION_MS,
    TOOL_FAILURES,
)
from relay_teams.metrics.models import (
    MetricScope,
    MetricTagSet,
    MetricsScopeSelector,
    ObservabilityBreakdown,
    ObservabilityBreakdownRow,
    ObservabilityGatewayBreakdownRow,
    ObservabilityKpiSet,
    ObservabilityOverview,
    ObservabilityRoleBreakdownRow,
    ObservabilityTrendPoint,
)
from relay_teams.metrics.stores.sqlite import (
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
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        output_tokens = totals[LLM_OUTPUT_TOKENS.name]
        retrieval_searches = totals[RETRIEVAL_SEARCHES.name]
        retrieval_failures = totals[RETRIEVAL_SEARCH_FAILURES.name]
        retrieval_duration_ms = totals[RETRIEVAL_SEARCH_DURATION_MS.name]
        gateway_summary = _build_gateway_summary(rows)
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
                uncached_input_tokens=uncached_input_tokens,
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
                retrieval_searches=retrieval_searches,
                retrieval_failure_rate=(
                    retrieval_failures / retrieval_searches
                    if retrieval_searches > 0
                    else 0
                ),
                retrieval_avg_duration_ms=(
                    retrieval_duration_ms / retrieval_searches
                    if retrieval_searches > 0
                    else 0
                ),
                retrieval_document_count=_latest_metric_total(
                    rows=rows,
                    metric_name=RETRIEVAL_DOCUMENT_COUNT.name,
                    key_builder=lambda tags: (
                        tags.retrieval_backend,
                        tags.retrieval_scope_kind,
                    ),
                ),
                gateway_calls=gateway_summary["request_calls"],
                gateway_failure_rate=(
                    gateway_summary["request_failures"]
                    / gateway_summary["request_calls"]
                    if gateway_summary["request_calls"] > 0
                    else 0
                ),
                gateway_avg_duration_ms=(
                    gateway_summary["request_duration_ms"]
                    / gateway_summary["request_calls"]
                    if gateway_summary["request_calls"] > 0
                    else 0
                ),
                gateway_prompt_avg_start_ms=(
                    gateway_summary["prompt_run_start_duration_ms"]
                    / gateway_summary["prompt_run_start_calls"]
                    if gateway_summary["prompt_run_start_calls"] > 0
                    else 0
                ),
                gateway_prompt_avg_first_update_ms=(
                    gateway_summary["prompt_first_update_duration_ms"]
                    / gateway_summary["prompt_first_update_calls"]
                    if gateway_summary["prompt_first_update_calls"] > 0
                    else 0
                ),
                gateway_mcp_calls=gateway_summary["mcp_request_calls"],
                gateway_cold_start_calls=gateway_summary["cold_start_calls"],
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
        tool_grouped: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
            lambda: {"calls": 0.0, "failures": 0.0, "duration_ms": 0.0}
        )
        role_grouped: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "input_tokens": 0.0,
                "cached_input_tokens": 0.0,
                "output_tokens": 0.0,
                "tool_calls": 0.0,
                "tool_failures": 0.0,
            }
        )
        gateway_grouped: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
            lambda: {
                "calls": 0.0,
                "failures": 0.0,
                "duration_ms": 0.0,
                "cold_start_calls": 0.0,
            }
        )
        for row in rows:
            tags = _parse_tags(row.tags_json)
            tool_name = tags.tool_name
            if tool_name:
                key = (tool_name, tags.tool_source, tags.mcp_server)
                if row.metric_name == TOOL_CALLS.name:
                    tool_grouped[key]["calls"] += row.value
                elif row.metric_name == TOOL_FAILURES.name:
                    tool_grouped[key]["failures"] += row.value
                elif row.metric_name == TOOL_DURATION_MS.name:
                    tool_grouped[key]["duration_ms"] += row.value
            role_id = tags.role_id or "unknown"
            if row.metric_name == LLM_INPUT_TOKENS.name:
                role_grouped[role_id]["input_tokens"] += row.value
            elif row.metric_name == LLM_CACHED_INPUT_TOKENS.name:
                role_grouped[role_id]["cached_input_tokens"] += row.value
            elif row.metric_name == LLM_OUTPUT_TOKENS.name:
                role_grouped[role_id]["output_tokens"] += row.value
            elif row.metric_name == TOOL_CALLS.name:
                role_grouped[role_id]["tool_calls"] += row.value
            elif row.metric_name == TOOL_FAILURES.name:
                role_grouped[role_id]["tool_failures"] += row.value
            if tags.gateway_operation and tags.gateway_phase and tags.gateway_transport:
                gateway_key = (
                    tags.gateway_operation,
                    tags.gateway_phase,
                    tags.gateway_transport,
                )
                if row.metric_name == GATEWAY_OPERATIONS.name:
                    gateway_grouped[gateway_key]["calls"] += row.value
                    if tags.gateway_cold_start == "true":
                        gateway_grouped[gateway_key]["cold_start_calls"] += row.value
                elif row.metric_name == GATEWAY_OPERATION_FAILURES.name:
                    gateway_grouped[gateway_key]["failures"] += row.value
                elif row.metric_name == GATEWAY_OPERATION_DURATION_MS.name:
                    gateway_grouped[gateway_key]["duration_ms"] += row.value
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
                for (tool_name, tool_source, mcp_server), values in tool_grouped.items()
            ),
            key=lambda item: (-item.calls, item.tool_name),
        )
        ordered_role_rows = sorted(
            (
                ObservabilityRoleBreakdownRow(
                    role_id=role_id,
                    input_tokens=values["input_tokens"],
                    cached_input_tokens=values["cached_input_tokens"],
                    uncached_input_tokens=max(
                        values["input_tokens"] - values["cached_input_tokens"],
                        0,
                    ),
                    output_tokens=values["output_tokens"],
                    tool_calls=values["tool_calls"],
                    tool_failures=values["tool_failures"],
                    tool_success_rate=(
                        (values["tool_calls"] - values["tool_failures"])
                        / values["tool_calls"]
                        if values["tool_calls"] > 0
                        else 0
                    ),
                    cached_token_ratio=(
                        values["cached_input_tokens"] / values["input_tokens"]
                        if values["input_tokens"] > 0
                        else 0
                    ),
                )
                for role_id, values in role_grouped.items()
                if any(metric_value > 0 for metric_value in values.values())
            ),
            key=lambda item: (-item.input_tokens, -item.tool_calls, item.role_id),
        )
        ordered_gateway_rows = sorted(
            (
                ObservabilityGatewayBreakdownRow(
                    gateway_operation=gateway_operation,
                    gateway_phase=gateway_phase,
                    gateway_transport=gateway_transport,
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
                    cold_start_calls=values["cold_start_calls"],
                )
                for (
                    gateway_operation,
                    gateway_phase,
                    gateway_transport,
                ), values in gateway_grouped.items()
            ),
            key=lambda item: (-item.calls, item.gateway_operation, item.gateway_phase),
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
            role_rows=tuple(ordered_role_rows),
            gateway_rows=tuple(ordered_gateway_rows),
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
    trend_points: list[ObservabilityTrendPoint] = []
    for bucket_start, values in sorted(buckets.items()):
        steps = values[SESSION_STEPS.name]
        input_tokens = values[LLM_INPUT_TOKENS.name]
        output_tokens = values[LLM_OUTPUT_TOKENS.name]
        tool_calls = values[TOOL_CALLS.name]
        if steps <= 0 and input_tokens <= 0 and output_tokens <= 0 and tool_calls <= 0:
            continue
        trend_points.append(
            ObservabilityTrendPoint(
                bucket_start=bucket_start,
                steps=steps,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls,
            )
        )
    return tuple(trend_points)


def _build_gateway_summary(rows: tuple[MetricPointRecord, ...]) -> dict[str, float]:
    summary: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        tags = _parse_tags(row.tags_json)
        if tags.gateway_phase == "request":
            if row.metric_name == GATEWAY_OPERATIONS.name:
                summary["request_calls"] += row.value
                if tags.gateway_cold_start == "true":
                    summary["cold_start_calls"] += row.value
                if _is_mcp_gateway_operation(tags.gateway_operation):
                    summary["mcp_request_calls"] += row.value
            elif row.metric_name == GATEWAY_OPERATION_FAILURES.name:
                summary["request_failures"] += row.value
            elif row.metric_name == GATEWAY_OPERATION_DURATION_MS.name:
                summary["request_duration_ms"] += row.value
        if tags.gateway_operation != "session_prompt":
            continue
        if tags.gateway_phase == "run_start":
            if row.metric_name == GATEWAY_OPERATIONS.name:
                summary["prompt_run_start_calls"] += row.value
            elif row.metric_name == GATEWAY_OPERATION_DURATION_MS.name:
                summary["prompt_run_start_duration_ms"] += row.value
            continue
        if tags.gateway_phase != "first_update":
            continue
        if row.metric_name == GATEWAY_OPERATIONS.name:
            summary["prompt_first_update_calls"] += row.value
        elif row.metric_name == GATEWAY_OPERATION_DURATION_MS.name:
            summary["prompt_first_update_duration_ms"] += row.value
    return summary


def _latest_metric_total(
    *,
    rows: tuple[MetricPointRecord, ...],
    metric_name: str,
    key_builder: Callable[[MetricTagSet], tuple[str, ...]],
) -> float:
    latest_rows: dict[tuple[str, ...], MetricPointRecord] = {}
    for row in rows:
        if row.metric_name != metric_name:
            continue
        tags = _parse_tags(row.tags_json)
        key = key_builder(tags)
        current = latest_rows.get(key)
        if current is None or row.recorded_at >= current.recorded_at:
            latest_rows[key] = row
    return sum(record.value for record in latest_rows.values())


def _parse_tags(raw_tags: str) -> MetricTagSet:
    parsed = json.loads(raw_tags)
    if not isinstance(parsed, dict):
        return MetricTagSet()
    normalized = {
        str(key): str(value) for key, value in parsed.items() if isinstance(key, str)
    }
    return MetricTagSet(**normalized)


def _is_mcp_gateway_operation(gateway_operation: str) -> bool:
    return gateway_operation in {
        "mcp_connect",
        "mcp_message",
        "mcp_disconnect",
        "mcp_bridge_request",
    }
