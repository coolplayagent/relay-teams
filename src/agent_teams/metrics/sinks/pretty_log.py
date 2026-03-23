# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from agent_teams.logger import get_logger
from agent_teams.metrics.models import MetricEvent

LOGGER = get_logger(__name__)


class PrettyLogSink:
    def record(self, event: MetricEvent) -> None:
        rendered_tags = ", ".join(
            f"{key}={value}" for key, value in event.tags.normalized_items()
        )
        LOGGER.log(
            logging.DEBUG,
            "[metrics] %s value=%s %s",
            event.definition_name,
            event.value,
            rendered_tags,
        )
