# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol, runtime_checkable

from relay_teams.metrics.models import MetricEvent


class MetricsSink(Protocol):
    def record(self, event: MetricEvent) -> None: ...


@runtime_checkable
class AsyncMetricsSink(Protocol):
    async def record_async(self, event: MetricEvent) -> None: ...
