# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.metrics.models import MetricEvent, MetricTagSet
from relay_teams.metrics.registry import MetricRegistry
from relay_teams.metrics.sinks.base import MetricsSink


class MetricRecorder:
    def __init__(
        self,
        *,
        registry: MetricRegistry,
        sinks: tuple[MetricsSink, ...] = (),
    ) -> None:
        self._registry = registry
        self._sinks = sinks

    def emit(
        self,
        *,
        definition_name: str,
        value: float,
        tags: MetricTagSet,
        occurred_at: datetime | None = None,
    ) -> None:
        definition = self._registry.get(definition_name)
        event = MetricEvent(
            definition_name=definition.name,
            kind=definition.kind,
            value=float(value),
            tags=tags,
            occurred_at=occurred_at or datetime.now(tz=timezone.utc),
        )
        for sink in self._sinks:
            try:
                sink.record(event)
            except Exception:
                continue
