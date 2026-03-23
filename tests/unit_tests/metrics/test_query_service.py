from __future__ import annotations

from datetime import datetime, timezone

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
    assert overview.kpis.input_tokens == 120
    assert overview.kpis.cached_input_tokens == 48
    assert overview.kpis.output_tokens == 24
    assert round(overview.kpis.cached_token_ratio, 2) == 0.40
    assert overview.kpis.tool_calls == 2
    assert round(overview.kpis.tool_success_rate, 2) == 0.50
    assert overview.kpis.tool_avg_duration_ms == 150
    assert len(overview.trends) == 1
    assert len(breakdown.rows) == 1
    assert breakdown.rows[0].tool_name == "shell"
    assert breakdown.rows[0].calls == 2
    assert breakdown.rows[0].failures == 1
