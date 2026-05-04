"""Coverage tests for MetricsService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.metrics.service import MetricsService


@pytest.mark.asyncio
async def test_get_overview_async_delegates() -> None:
    mock_self = MagicMock()
    method = MetricsService.get_overview_async
    await method(
        mock_self,
        scope=cast(Any, ""),
        scope_id=cast(Any, ""),
        time_window_minutes=cast(Any, ""),
    )
    getattr(mock_self, "get_overview").assert_called_once()


@pytest.mark.asyncio
async def test_get_breakdowns_async_delegates() -> None:
    mock_self = MagicMock()
    method = MetricsService.get_breakdowns_async
    await method(
        mock_self,
        scope=cast(Any, ""),
        scope_id=cast(Any, ""),
        time_window_minutes=cast(Any, ""),
    )
    getattr(mock_self, "get_breakdowns").assert_called_once()
