# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.metrics.models import MetricEvent
from relay_teams.metrics.stores.sqlite import SqliteMetricAggregateStore


class AggregateStoreSink:
    def __init__(self, store: SqliteMetricAggregateStore) -> None:
        self._store = store

    def record(self, event: MetricEvent) -> None:
        self._store.record(event)

    async def record_async(self, event: MetricEvent) -> None:
        await self._store.record_async(event)
