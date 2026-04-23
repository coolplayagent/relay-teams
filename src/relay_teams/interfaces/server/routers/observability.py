from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from relay_teams.interfaces.server.deps import get_metrics_service
from relay_teams.metrics import MetricScope, MetricsService

router = APIRouter(prefix="/observability", tags=["Observability"])


@router.get("/overview")
async def get_observability_overview(
    scope: MetricScope = MetricScope.GLOBAL,
    scope_id: str = "",
    time_window_minutes: int = 1440,
    service: MetricsService = Depends(get_metrics_service),
) -> dict[str, object]:
    if scope != MetricScope.GLOBAL and not scope_id.strip():
        raise HTTPException(status_code=422, detail="scope_id is required")
    overview = service.get_overview(
        scope=scope,
        scope_id=scope_id,
        time_window_minutes=time_window_minutes,
    )
    return overview.model_dump(mode="json")


@router.get("/breakdowns")
async def get_observability_breakdowns(
    scope: MetricScope = MetricScope.GLOBAL,
    scope_id: str = "",
    time_window_minutes: int = 1440,
    service: MetricsService = Depends(get_metrics_service),
) -> dict[str, object]:
    if scope != MetricScope.GLOBAL and not scope_id.strip():
        raise HTTPException(status_code=422, detail="scope_id is required")
    breakdown = service.get_breakdowns(
        scope=scope,
        scope_id=scope_id,
        time_window_minutes=time_window_minutes,
    )
    return breakdown.model_dump(mode="json")
