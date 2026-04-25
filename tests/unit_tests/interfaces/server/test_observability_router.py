from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.deps import get_metrics_service
from relay_teams.interfaces.server.routers import observability
from relay_teams.metrics import (
    MetricScope,
    ObservabilityBreakdown,
    ObservabilityBreakdownRow,
    ObservabilityGatewayBreakdownRow,
    ObservabilityKpiSet,
    ObservabilityOverview,
    ObservabilityRoleBreakdownRow,
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
            kpis=ObservabilityKpiSet(
                steps=3,
                tool_calls=2,
                uncached_input_tokens=9,
                retrieval_searches=4,
                retrieval_failure_rate=0.25,
                gateway_calls=5,
                gateway_avg_duration_ms=120,
            ),
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
            role_rows=(
                ObservabilityRoleBreakdownRow(
                    role_id="coordinator",
                    input_tokens=10,
                    cached_input_tokens=4,
                    uncached_input_tokens=6,
                    tool_calls=2,
                    tool_success_rate=1.0,
                ),
            ),
            gateway_rows=(
                ObservabilityGatewayBreakdownRow(
                    gateway_operation="session_prompt",
                    gateway_phase="request",
                    gateway_transport="stdio",
                    calls=2,
                    success_rate=1.0,
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
    assert response.json()["kpis"]["retrieval_searches"] == 4
    assert response.json()["kpis"]["uncached_input_tokens"] == 9
    assert response.json()["kpis"]["gateway_calls"] == 5


def test_observability_breakdowns_route_returns_role_rows() -> None:
    client = _create_client()
    response = client.get(
        "/api/observability/breakdowns?scope=global&time_window_minutes=60"
    )
    assert response.status_code == 200
    assert response.json()["rows"][0]["tool_name"] == "shell"
    assert response.json()["role_rows"][0]["role_id"] == "coordinator"
    assert response.json()["gateway_rows"][0]["gateway_operation"] == "session_prompt"


def test_observability_breakdowns_route_requires_scope_id() -> None:
    client = _create_client()
    response = client.get("/api/observability/breakdowns?scope=session")
    assert response.status_code == 422
    assert response.json() == {"detail": "scope_id is required"}


def test_observability_routes_offload_sync_service_calls(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_to_thread(
        func: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(observability, "call_maybe_async", fake_to_thread)
    client = _create_client()

    overview = client.get(
        "/api/observability/overview?scope=session&scope_id=session-1&time_window_minutes=60"
    )
    breakdowns = client.get(
        "/api/observability/breakdowns?scope=global&time_window_minutes=60"
    )

    assert overview.status_code == 200
    assert breakdowns.status_code == 200
    assert calls == [
        (
            "get_overview",
            (),
            {
                "scope": MetricScope.SESSION,
                "scope_id": "session-1",
                "time_window_minutes": 60,
            },
        ),
        (
            "get_breakdowns",
            (),
            {
                "scope": MetricScope.GLOBAL,
                "scope_id": "",
                "time_window_minutes": 60,
            },
        ),
    ]
