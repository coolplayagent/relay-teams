"""Coverage tests for TriggerService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.triggers.service import GitHubTriggerService


@pytest.mark.asyncio
async def test_list_accounts_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.list_accounts_async
    await method(mock_self)
    getattr(mock_self, "list_accounts").assert_called_once()


@pytest.mark.asyncio
async def test_create_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.create_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "create_account").assert_called_once()


@pytest.mark.asyncio
async def test_update_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.update_account_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_account").assert_called_once()


@pytest.mark.asyncio
async def test_delete_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.delete_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_account").assert_called_once()


@pytest.mark.asyncio
async def test_enable_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.enable_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "enable_account").assert_called_once()


@pytest.mark.asyncio
async def test_disable_account_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.disable_account_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "disable_account").assert_called_once()


@pytest.mark.asyncio
async def test_list_repo_subscriptions_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.list_repo_subscriptions_async
    await method(mock_self)
    getattr(mock_self, "list_repo_subscriptions").assert_called_once()


@pytest.mark.asyncio
async def test_list_available_repositories_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.list_available_repositories_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "list_available_repositories").assert_called_once()


@pytest.mark.asyncio
async def test_create_repo_subscription_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.create_repo_subscription_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "create_repo_subscription").assert_called_once()


@pytest.mark.asyncio
async def test_update_repo_subscription_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.update_repo_subscription_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_repo_subscription").assert_called_once()


@pytest.mark.asyncio
async def test_delete_repo_subscription_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.delete_repo_subscription_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_repo_subscription").assert_called_once()


@pytest.mark.asyncio
async def test_enable_repo_subscription_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.enable_repo_subscription_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "enable_repo_subscription").assert_called_once()


@pytest.mark.asyncio
async def test_disable_repo_subscription_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.disable_repo_subscription_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "disable_repo_subscription").assert_called_once()


@pytest.mark.asyncio
async def test_list_rules_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.list_rules_async
    await method(mock_self)
    getattr(mock_self, "list_rules").assert_called_once()


@pytest.mark.asyncio
async def test_create_rule_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.create_rule_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "create_rule").assert_called_once()


@pytest.mark.asyncio
async def test_update_rule_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.update_rule_async
    await method(mock_self, cast(Any, ""), cast(Any, ""))
    getattr(mock_self, "update_rule").assert_called_once()


@pytest.mark.asyncio
async def test_delete_rule_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.delete_rule_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "delete_rule").assert_called_once()


@pytest.mark.asyncio
async def test_enable_rule_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.enable_rule_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "enable_rule").assert_called_once()


@pytest.mark.asyncio
async def test_disable_rule_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.disable_rule_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "disable_rule").assert_called_once()


@pytest.mark.asyncio
async def test_handle_inbound_github_delivery_async_delegates() -> None:
    mock_self = MagicMock()
    method = GitHubTriggerService.handle_inbound_github_delivery_async
    await method(mock_self, headers=cast(Any, ""), body=cast(Any, ""))
    getattr(mock_self, "handle_inbound_github_delivery").assert_called_once()
