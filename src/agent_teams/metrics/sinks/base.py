# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from agent_teams.metrics.models import MetricEvent


class MetricsSink(Protocol):
    def record(self, event: MetricEvent) -> None: ...
