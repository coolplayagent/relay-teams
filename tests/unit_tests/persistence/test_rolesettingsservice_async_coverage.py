"""Coverage tests for RoleSettingsService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.roles.settings_service import RoleSettingsService


@pytest.mark.asyncio
async def test_list_role_documents_async_delegates() -> None:
    mock_self = MagicMock()
    method = RoleSettingsService.list_role_documents_async
    await method(mock_self)
    getattr(mock_self, "list_role_documents").assert_called_once()


@pytest.mark.asyncio
async def test_get_role_document_async_delegates() -> None:
    mock_self = MagicMock()
    method = RoleSettingsService.get_role_document_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_role_document").assert_called_once()


@pytest.mark.asyncio
async def test_save_role_document_async_delegates() -> None:
    mock_self = MagicMock()
    method = RoleSettingsService.save_role_document_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "save_role_document").assert_called_once()


@pytest.mark.asyncio
async def test_delete_role_document_async_delegates() -> None:
    mock_self = MagicMock()
    method = RoleSettingsService.delete_role_document_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_role_document").assert_called_once()


@pytest.mark.asyncio
async def test_validate_all_roles_async_delegates() -> None:
    mock_self = MagicMock()
    method = RoleSettingsService.validate_all_roles_async
    await method(mock_self)
    getattr(mock_self, "validate_all_roles").assert_called_once()


@pytest.mark.asyncio
async def test_validate_role_document_async_delegates() -> None:
    mock_self = MagicMock()
    method = RoleSettingsService.validate_role_document_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "validate_role_document").assert_called_once()
