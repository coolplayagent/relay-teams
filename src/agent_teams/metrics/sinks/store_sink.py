# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.metrics.models import MetricEvent
from agent_teams.metrics.stores.sqlite import SqliteMetricAggregateStore


class AggregateStoreSink:
    def __init__(self, store: SqliteMetricAggregateStore) -> None:
        self._store = store

    def record(self, event: MetricEvent) -> None:
        self._store.record(event)
