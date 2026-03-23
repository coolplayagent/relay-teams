from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_metrics_service
from agent_teams.interfaces.server.routers import observability
from agent_teams.metrics import (
    MetricScope,
    ObservabilityBreakdown,
    ObservabilityBreakdownRow,
    ObservabilityKpiSet,
    ObservabilityOverview,
    ObservabilityTrendPoint,
)


class _FakeMetricsService:
    def get_overview(
        self, *, scope: MetricScope, scope_id: str, time_window_minutes: int
    ) -> ObservabilityOverview:
        return ObservabilityOverview(
            scope=scope,
            scope_id=scope_id,
            time_window_minutes=time_window_minutes,
            updated_at="2026-03-23T10:00:00+00:00",
            kpis=ObservabilityKpiSet(steps=3, tool_calls=2),
            trends=(
                ObservabilityTrendPoint(
                    bucket_start="2026-03-23T10:00:00+00:00", steps=3, tool_calls=2
                ),
            ),
        )

    def get_breakdowns(
        self, *, scope: MetricScope, scope_id: str, time_window_minutes: int
    ) -> ObservabilityBreakdown:
        return ObservabilityBreakdown(
            scope=scope,
            scope_id=scope_id,
            time_window_minutes=time_window_minutes,
            updated_at="2026-03-23T10:00:00+00:00",
            rows=(
                ObservabilityBreakdownRow(
                    tool_name="shell", tool_source="local", calls=2, success_rate=1.0
                ),
            ),
        )


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(observability.router, prefix="/api")
    app.dependency_overrides[get_metrics_service] = lambda: _FakeMetricsService()
    return TestClient(app)


def test_observability_overview_route_returns_payload() -> None:
    client = _create_client()
    response = client.get(
        "/api/observability/overview?scope=session&scope_id=session-1&time_window_minutes=60"
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "session"
    assert response.json()["kpis"]["steps"] == 3


def test_observability_breakdowns_route_requires_scope_id() -> None:
    client = _create_client()
    response = client.get("/api/observability/breakdowns?scope=session")
    assert response.status_code == 422
    assert response.json() == {"detail": "scope_id is required"}
