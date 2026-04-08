# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.metrics.models import (
    MetricScope,
    MetricsScopeSelector,
    ObservabilityBreakdown,
    ObservabilityOverview,
)
from relay_teams.metrics.query_service import MetricsQueryService


class MetricsService:
    def __init__(self, *, query_service: MetricsQueryService) -> None:
        self._query_service = query_service

    def get_overview(
        self,
        *,
        scope: MetricScope,
        scope_id: str,
        time_window_minutes: int,
    ) -> ObservabilityOverview:
        return self._query_service.get_overview(
            MetricsScopeSelector(
                scope=scope,
                scope_id=scope_id,
                time_window_minutes=time_window_minutes,
            )
        )

    def get_breakdowns(
        self,
        *,
        scope: MetricScope,
        scope_id: str,
        time_window_minutes: int,
    ) -> ObservabilityBreakdown:
        return self._query_service.get_breakdowns(
            MetricsScopeSelector(
                scope=scope,
                scope_id=scope_id,
                time_window_minutes=time_window_minutes,
            )
        )
