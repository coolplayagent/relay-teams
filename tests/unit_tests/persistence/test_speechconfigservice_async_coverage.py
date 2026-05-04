"""Coverage tests for SpeechConfigService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.speech.config_service import SpeechConfigService


@pytest.mark.asyncio
async def test_get_config_payload_async_delegates() -> None:
    mock_self = MagicMock()
    method = SpeechConfigService.get_config_payload_async
    await method(mock_self)
    getattr(mock_self, "get_config_payload").assert_called_once()


@pytest.mark.asyncio
async def test_save_config_async_delegates() -> None:
    mock_self = MagicMock()
    method = SpeechConfigService.save_config_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "save_config").assert_called_once()
