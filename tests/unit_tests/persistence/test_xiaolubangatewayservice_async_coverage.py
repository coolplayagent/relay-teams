"""Coverage tests for XiaolubanGatewayService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.gateway.xiaoluban.service import XiaolubanGatewayService


@pytest.mark.asyncio
async def test_list_accounts_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.list_accounts_async
    await method(mock_self)
    getattr(mock_self, "list_accounts").assert_called_once()


@pytest.mark.asyncio
async def test_get_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.get_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_account").assert_called_once()


@pytest.mark.asyncio
async def test_create_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.create_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "create_account").assert_called_once()


@pytest.mark.asyncio
async def test_update_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.update_account_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_account").assert_called_once()


@pytest.mark.asyncio
async def test_prepare_account_id_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.prepare_account_id_async
    await method(mock_self)
    getattr(mock_self, "prepare_account_id").assert_called_once()


@pytest.mark.asyncio
async def test_reveal_token_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.reveal_token_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "reveal_token").assert_called_once()


@pytest.mark.asyncio
async def test_update_im_config_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.update_im_config_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_im_config").assert_called_once()


@pytest.mark.asyncio
async def test_set_account_enabled_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.set_account_enabled_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "set_account_enabled").assert_called_once()


@pytest.mark.asyncio
async def test_delete_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.delete_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_account").assert_called_once()


@pytest.mark.asyncio
async def test_get_im_callback_auth_token_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.get_im_callback_auth_token_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_im_callback_auth_token").assert_called_once()


@pytest.mark.asyncio
async def test_validate_im_workspace_async_delegates() -> None:
    mock_self = MagicMock()
    method = XiaolubanGatewayService.validate_im_workspace_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "validate_im_workspace").assert_called_once()
