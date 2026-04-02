from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_teams.metrics import (
    DEFAULT_DEFINITIONS,
    MetricRecorder,
    MetricRegistry,
    MetricTagSet,
    MetricScope,
    MetricsQueryService,
    MetricsScopeSelector,
    SqliteMetricAggregateStore,
)
from agent_teams.metrics.sinks import AggregateStoreSink


def test_metrics_query_service_builds_overview_and_breakdowns(tmp_path) -> None:
    store = SqliteMetricAggregateStore(tmp_path / "metrics.db")
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(AggregateStoreSink(store),),
    )
    now = datetime.now(tz=timezone.utc)
    tags = MetricTagSet(
        workspace_id="default",
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="coordinator",
        tool_name="shell",
        tool_source="local",
        status="success",
    )
    recorder.emit(
        definition_name="agent_teams.session.steps", value=2, tags=tags, occurred_at=now
    )
    recorder.emit(
        definition_name="agent_teams.llm.input_tokens",
        value=120,
        tags=tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.llm.cached_input_tokens",
        value=48,
        tags=tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.llm.output_tokens",
        value=24,
        tags=tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.tool.calls", value=2, tags=tags, occurred_at=now
    )
    recorder.emit(
        definition_name="agent_teams.tool.duration_ms",
        value=300,
        tags=tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.tool.failures",
        value=1,
        tags=MetricTagSet(**(tags.model_dump() | {"status": "failure"})),
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.retrieval.searches",
        value=4,
        tags=MetricTagSet(
            session_id="session-1",
            run_id="run-1",
            instance_id="inst-1",
            role_id="coordinator",
            retrieval_backend="sqlite_fts",
            retrieval_scope_kind="skill",
            retrieval_operation="search",
            status="success",
        ),
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.retrieval.search_duration_ms",
        value=200,
        tags=MetricTagSet(
            session_id="session-1",
            run_id="run-1",
            instance_id="inst-1",
            role_id="coordinator",
            retrieval_backend="sqlite_fts",
            retrieval_scope_kind="skill",
            retrieval_operation="search",
            status="success",
        ),
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.retrieval.search_failures",
        value=1,
        tags=MetricTagSet(
            session_id="session-1",
            run_id="run-1",
            instance_id="inst-1",
            role_id="coordinator",
            retrieval_backend="sqlite_fts",
            retrieval_scope_kind="skill",
            retrieval_operation="search",
            status="failure",
        ),
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.retrieval.document_count",
        value=10,
        tags=MetricTagSet(
            session_id="session-1",
            run_id="run-1",
            instance_id="inst-1",
            role_id="coordinator",
            retrieval_backend="sqlite_fts",
            retrieval_scope_kind="skill",
            retrieval_operation="rebuild",
            status="success",
        ),
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.retrieval.document_count",
        value=12,
        tags=MetricTagSet(
            session_id="session-1",
            run_id="run-1",
            instance_id="inst-1",
            role_id="coordinator",
            retrieval_backend="sqlite_fts",
            retrieval_scope_kind="skill",
            retrieval_operation="sync",
            status="success",
        ),
        occurred_at=now + timedelta(minutes=1),
    )
    recorder.emit(
        definition_name="agent_teams.retrieval.document_count",
        value=5,
        tags=MetricTagSet(
            session_id="session-1",
            run_id="run-1",
            instance_id="inst-1",
            role_id="coordinator",
            retrieval_backend="sqlite_fts",
            retrieval_scope_kind="workspace",
            retrieval_operation="sync",
            status="success",
        ),
        occurred_at=now + timedelta(minutes=1),
    )
    reviewer_tags = MetricTagSet(
        workspace_id="default",
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-2",
        role_id="reviewer",
        tool_name="browser",
        tool_source="mcp",
        mcp_server="chrome-devtools",
        status="success",
    )
    recorder.emit(
        definition_name="agent_teams.llm.input_tokens",
        value=30,
        tags=reviewer_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.llm.output_tokens",
        value=12,
        tags=reviewer_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.tool.calls",
        value=1,
        tags=reviewer_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.tool.duration_ms",
        value=80,
        tags=reviewer_tags,
        occurred_at=now,
    )
    gateway_request_tags = MetricTagSet(
        session_id="session-1",
        run_id="run-1",
        gateway_channel="acp_stdio",
        gateway_operation="session_prompt",
        gateway_phase="request",
        gateway_transport="stdio",
        gateway_cold_start="true",
        status="completed",
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operations",
        value=1,
        tags=gateway_request_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operation_duration_ms",
        value=420,
        tags=gateway_request_tags,
        occurred_at=now,
    )
    gateway_prompt_start_tags = MetricTagSet(
        session_id="session-1",
        run_id="run-1",
        gateway_channel="acp_stdio",
        gateway_operation="session_prompt",
        gateway_phase="run_start",
        gateway_transport="stdio",
        gateway_cold_start="true",
        status="success",
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operations",
        value=1,
        tags=gateway_prompt_start_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operation_duration_ms",
        value=110,
        tags=gateway_prompt_start_tags,
        occurred_at=now,
    )
    gateway_first_update_tags = MetricTagSet(
        session_id="session-1",
        run_id="run-1",
        gateway_channel="acp_stdio",
        gateway_operation="session_prompt",
        gateway_phase="first_update",
        gateway_transport="stdio",
        gateway_cold_start="true",
        status="success",
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operations",
        value=1,
        tags=gateway_first_update_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operation_duration_ms",
        value=180,
        tags=gateway_first_update_tags,
        occurred_at=now,
    )
    gateway_mcp_tags = MetricTagSet(
        session_id="session-1",
        gateway_channel="acp_stdio",
        gateway_operation="mcp_bridge_request",
        gateway_phase="request",
        gateway_transport="acp",
        gateway_cold_start="false",
        status="failed",
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operations",
        value=1,
        tags=gateway_mcp_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operation_duration_ms",
        value=75,
        tags=gateway_mcp_tags,
        occurred_at=now,
    )
    recorder.emit(
        definition_name="agent_teams.gateway.operation_failures",
        value=1,
        tags=gateway_mcp_tags,
        occurred_at=now,
    )

    query = MetricsQueryService(store=store)
    overview = query.get_overview(
        MetricsScopeSelector(
            scope=MetricScope.SESSION, scope_id="session-1", time_window_minutes=60
        )
    )
    breakdown = query.get_breakdowns(
        MetricsScopeSelector(
            scope=MetricScope.SESSION, scope_id="session-1", time_window_minutes=60
        )
    )

    assert overview.kpis.steps == 2
    assert overview.kpis.input_tokens == 150
    assert overview.kpis.cached_input_tokens == 48
    assert overview.kpis.uncached_input_tokens == 102
    assert overview.kpis.output_tokens == 36
    assert round(overview.kpis.cached_token_ratio, 2) == 0.32
    assert overview.kpis.tool_calls == 3
    assert round(overview.kpis.tool_success_rate, 2) == 0.67
    assert round(overview.kpis.tool_avg_duration_ms, 2) == 126.67
    assert overview.kpis.retrieval_searches == 4
    assert round(overview.kpis.retrieval_failure_rate, 2) == 0.25
    assert overview.kpis.retrieval_avg_duration_ms == 50
    assert overview.kpis.retrieval_document_count == 17
    assert overview.kpis.gateway_calls == 2
    assert round(overview.kpis.gateway_failure_rate, 2) == 0.5
    assert round(overview.kpis.gateway_avg_duration_ms, 2) == 247.5
    assert overview.kpis.gateway_prompt_avg_start_ms == 110
    assert overview.kpis.gateway_prompt_avg_first_update_ms == 180
    assert overview.kpis.gateway_mcp_calls == 1
    assert overview.kpis.gateway_cold_start_calls == 1
    assert len(overview.trends) == 1
    assert len(breakdown.rows) == 2
    assert breakdown.rows[0].tool_name == "shell"
    assert breakdown.rows[0].calls == 2
    assert breakdown.rows[0].failures == 1
    assert breakdown.rows[1].tool_name == "browser"
    assert breakdown.rows[1].avg_duration_ms == 80
    assert len(breakdown.role_rows) == 2
    assert breakdown.role_rows[0].role_id == "coordinator"
    assert breakdown.role_rows[0].input_tokens == 120
    assert breakdown.role_rows[0].cached_input_tokens == 48
    assert breakdown.role_rows[0].uncached_input_tokens == 72
    assert breakdown.role_rows[0].tool_failures == 1
    assert round(breakdown.role_rows[0].cached_token_ratio, 2) == 0.40
    assert breakdown.role_rows[1].role_id == "reviewer"
    assert breakdown.role_rows[1].tool_calls == 1
    assert breakdown.role_rows[1].tool_success_rate == 1
    assert len(breakdown.gateway_rows) == 4
    gateway_rows = {
        (
            row.gateway_operation,
            row.gateway_phase,
            row.gateway_transport,
        ): row
        for row in breakdown.gateway_rows
    }
    session_prompt_request = gateway_rows[("session_prompt", "request", "stdio")]
    assert session_prompt_request.calls == 1
    assert session_prompt_request.cold_start_calls == 1
