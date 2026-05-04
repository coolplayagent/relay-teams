"""Coverage tests for FeishuGatewayService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.gateway.feishu.gateway_service import FeishuGatewayService


@pytest.mark.asyncio
async def test_list_accounts_async_delegates() -> None:
    mock_self = MagicMock()
    method = FeishuGatewayService.list_accounts_async
    await method(mock_self)
    getattr(mock_self, "list_accounts").assert_called_once()


@pytest.mark.asyncio
async def test_get_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = FeishuGatewayService.get_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_account").assert_called_once()


@pytest.mark.asyncio
async def test_create_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = FeishuGatewayService.create_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "create_account").assert_called_once()


@pytest.mark.asyncio
async def test_update_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = FeishuGatewayService.update_account_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_account").assert_called_once()


@pytest.mark.asyncio
async def test_set_account_enabled_async_delegates() -> None:
    mock_self = MagicMock()
    method = FeishuGatewayService.set_account_enabled_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "set_account_enabled").assert_called_once()


@pytest.mark.asyncio
async def test_delete_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = FeishuGatewayService.delete_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_account").assert_called_once()
