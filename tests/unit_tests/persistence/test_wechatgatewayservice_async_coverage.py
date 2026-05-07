"""Coverage tests for WeChatGatewayService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.gateway.wechat.service import WeChatGatewayService


@pytest.mark.asyncio
async def test_reload_async_delegates() -> None:
    mock_self = MagicMock()
    method = WeChatGatewayService.reload_async
    await method(mock_self)
    getattr(mock_self, "reload").assert_called_once()


@pytest.mark.asyncio
async def test_list_accounts_async_delegates() -> None:
    mock_self = MagicMock()
    method = WeChatGatewayService.list_accounts_async
    await method(mock_self)
    getattr(mock_self, "list_accounts").assert_called_once()


@pytest.mark.asyncio
async def test_start_login_async_delegates() -> None:
    mock_self = MagicMock()
    mock_self.start_login = AsyncMock()
    method = WeChatGatewayService.start_login_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "start_login").assert_called_once()


@pytest.mark.asyncio
async def test_wait_login_async_delegates() -> None:
    mock_self = MagicMock()
    mock_self.wait_login = AsyncMock()
    method = WeChatGatewayService.wait_login_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "wait_login").assert_called_once()


@pytest.mark.asyncio
async def test_update_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = WeChatGatewayService.update_account_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_account").assert_called_once()


@pytest.mark.asyncio
async def test_set_account_enabled_async_delegates() -> None:
    mock_self = MagicMock()
    method = WeChatGatewayService.set_account_enabled_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "set_account_enabled").assert_called_once()


@pytest.mark.asyncio
async def test_delete_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = WeChatGatewayService.delete_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_account").assert_called_once()
