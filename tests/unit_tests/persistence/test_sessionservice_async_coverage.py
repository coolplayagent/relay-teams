"""Coverage tests for SessionService async wrapper methods."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.sessions.session_service import SessionService


class _ReadThroughCache:
    async def read(
        self,
        refresh: Callable[[], object] | None = None,
        **kwargs: object,
    ) -> SimpleNamespace:
        refresh_fn = refresh
        if refresh_fn is None:
            candidate = kwargs["refresh"]
            if not callable(candidate):
                raise AssertionError("refresh callback is required")
            refresh_fn = candidate
        return SimpleNamespace(value=refresh_fn())


@pytest.mark.asyncio
async def test_list_sessions_async_delegates() -> None:
    mock_self = MagicMock()
    mock_self._session_list_cache = _ReadThroughCache()
    method = SessionService.list_sessions_async
    await method(mock_self)
    getattr(mock_self, "list_sessions").assert_called_once()


@pytest.mark.asyncio
async def test_list_normal_mode_subagents_async_delegates() -> None:
    mock_self = MagicMock()
    method = SessionService.list_normal_mode_subagents_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "list_normal_mode_subagents").assert_called_once()


@pytest.mark.asyncio
async def test_get_session_rounds_async_delegates() -> None:
    mock_self = MagicMock()
    mock_self._session_snapshot_cache = _ReadThroughCache()
    method = SessionService.get_session_rounds_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_session_rounds").assert_called_once()


@pytest.mark.asyncio
async def test_get_round_async_delegates() -> None:
    mock_self = MagicMock()
    method = SessionService.get_round_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "get_round").assert_called_once()


@pytest.mark.asyncio
async def test_get_recovery_snapshot_async_delegates() -> None:
    mock_self = MagicMock()
    mock_self._session_snapshot_cache = _ReadThroughCache()
    method = SessionService.get_recovery_snapshot_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_recovery_snapshot").assert_called_once()


@pytest.mark.asyncio
async def test_get_token_usage_by_run_async_delegates() -> None:
    mock_self = MagicMock()
    method = SessionService.get_token_usage_by_run_async
    await method(mock_self, cast(Any, ""))
    mock_self._token_usage_repo.get_by_run.assert_called_once()


@pytest.mark.asyncio
async def test_get_token_usage_by_session_async_delegates() -> None:
    mock_self = MagicMock()
    mock_self._session_snapshot_cache = _ReadThroughCache()
    method = SessionService.get_token_usage_by_session_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_token_usage_by_session").assert_called_once()
