# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.metrics.models import MetricEvent


class GrafanaExporterSink:
    """Placeholder sink for external Grafana-oriented exporters."""

    @staticmethod
    def record(event: MetricEvent) -> None:
        _ = event
        return None
