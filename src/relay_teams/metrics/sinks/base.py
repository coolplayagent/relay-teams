# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol, runtime_checkable

from relay_teams.metrics.models import MetricEvent


class MetricsSink(Protocol):
    @staticmethod
    def record(event: MetricEvent) -> None:
        pass


@runtime_checkable
class AsyncMetricsSink(Protocol):
    @staticmethod
    async def record_async(event: MetricEvent) -> None:
        pass
