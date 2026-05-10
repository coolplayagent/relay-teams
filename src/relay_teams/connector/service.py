# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol

from relay_teams.connector.models import (
    ConnectorAuthType,
    ConnectorCategory,
    ConnectorHealthCheck,
    ConnectorItem,
    ConnectorListResponse,
    ConnectorProvider,
    ConnectorStatus,
    ConnectorSummary,
    ConnectorTestResult,
)
from relay_teams.gateway.discord.models import (
    DiscordAccountRecord,
    DiscordAccountStatus,
)
from relay_teams.gateway.feishu.models import (
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
)
from relay_teams.gateway.wechat.models import WeChatAccountRecord, WeChatAccountStatus
from relay_teams.gateway.xiaoluban import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
)
from relay_teams.net.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
)
from relay_teams.triggers import GitHubTriggerAccountRecord, GitHubTriggerAccountStatus
from relay_teams.validation import RequiredIdentifierStr


class GitHubTriggerServiceLike(Protocol):
    async def list_accounts_async(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        raise NotImplementedError

    async def resolve_account_token_async(self, account_id: str) -> str | None:
        raise NotImplementedError


class FeishuGatewayServiceLike(Protocol):
    async def list_accounts_async(self) -> tuple[FeishuGatewayAccountRecord, ...]:
        raise NotImplementedError


class WeChatGatewayServiceLike(Protocol):
    async def list_accounts_async(self) -> tuple[WeChatAccountRecord, ...]:
        raise NotImplementedError


class DiscordGatewayServiceLike(Protocol):
    async def list_accounts(self) -> tuple[DiscordAccountRecord, ...]:
        raise NotImplementedError


class XiaolubanGatewayServiceLike(Protocol):
    async def list_accounts_async(self) -> tuple[XiaolubanAccountRecord, ...]:
        raise NotImplementedError


class GitHubConnectivityProbeServiceLike(Protocol):
    async def probe_async(
        self, request: GitHubConnectivityProbeRequest
    ) -> GitHubConnectivityProbeResult:
        raise NotImplementedError


class FeishuSubscriptionServiceLike(Protocol):
    def is_account_running(self, account_id: str) -> bool:
        raise NotImplementedError


class XiaolubanImListenerServiceLike(Protocol):
    def is_running(self) -> bool:
        raise NotImplementedError


CONNECTOR_PROVIDER_BY_ID: Mapping[str, ConnectorProvider] = {
    ConnectorProvider.GITHUB.value: ConnectorProvider.GITHUB,
    ConnectorProvider.DISCORD.value: ConnectorProvider.DISCORD,
    ConnectorProvider.FEISHU.value: ConnectorProvider.FEISHU,
    ConnectorProvider.WECHAT.value: ConnectorProvider.WECHAT,
    ConnectorProvider.XIAOLUBAN.value: ConnectorProvider.XIAOLUBAN,
}


class ConnectorService:
    def __init__(
        self,
        *,
        github_trigger_service: GitHubTriggerServiceLike,
        github_connectivity_probe_service: GitHubConnectivityProbeServiceLike,
        feishu_gateway_service: FeishuGatewayServiceLike,
        feishu_subscription_service: FeishuSubscriptionServiceLike,
        discord_gateway_service: DiscordGatewayServiceLike,
        wechat_gateway_service: WeChatGatewayServiceLike,
        xiaoluban_gateway_service: XiaolubanGatewayServiceLike,
        xiaoluban_im_listener_service: XiaolubanImListenerServiceLike,
    ) -> None:
        self._github_trigger_service = github_trigger_service
        self._github_connectivity_probe_service = github_connectivity_probe_service
        self._feishu_gateway_service = feishu_gateway_service
        self._feishu_subscription_service = feishu_subscription_service
        self._discord_gateway_service = discord_gateway_service
        self._wechat_gateway_service = wechat_gateway_service
        self._xiaoluban_gateway_service = xiaoluban_gateway_service
        self._xiaoluban_im_listener_service = xiaoluban_im_listener_service

    async def list_connectors(self) -> ConnectorListResponse:
        github_accounts = await self._github_trigger_service.list_accounts_async()
        feishu_accounts = await self._feishu_gateway_service.list_accounts_async()
        discord_accounts = await self._discord_gateway_service.list_accounts()
        wechat_accounts = await self._wechat_gateway_service.list_accounts_async()
        xiaoluban_accounts = await self._xiaoluban_gateway_service.list_accounts_async()
        items = (
            self._github_item(github_accounts),
            self._discord_item(discord_accounts),
            self._feishu_item(feishu_accounts),
            self._wechat_item(wechat_accounts),
            self._xiaoluban_item(xiaoluban_accounts),
        )
        return ConnectorListResponse(summary=self._summary(items), items=items)

    async def test_connector(
        self, connector_id: RequiredIdentifierStr
    ) -> ConnectorTestResult:
        provider = self._provider_for_connector_id(connector_id)
        if provider == ConnectorProvider.GITHUB:
            return await self._test_github()
        if provider == ConnectorProvider.FEISHU:
            return await self._test_feishu()
        if provider == ConnectorProvider.DISCORD:
            return await self._test_discord()
        if provider == ConnectorProvider.WECHAT:
            return await self._test_wechat()
        if provider == ConnectorProvider.XIAOLUBAN:
            return await self._test_xiaoluban()
        raise KeyError(f"Unknown connector_id: {connector_id}")

    @staticmethod
    def _github_item(
        accounts: Sequence[GitHubTriggerAccountRecord],
    ) -> ConnectorItem:
        enabled = tuple(
            account
            for account in accounts
            if account.status == GitHubTriggerAccountStatus.ENABLED
        )
        last_error = _first_error(account.last_error for account in accounts)
        configured = tuple(account for account in enabled if account.token_configured)
        return ConnectorItem(
            connector_id="github",
            provider=ConnectorProvider.GITHUB,
            category=ConnectorCategory.DEVELOPMENT,
            display_name="GitHub",
            description="Connect GitHub repositories, issues, pull requests, and Actions events.",
            status=_aggregate_status(
                account_count=len(accounts),
                enabled_count=len(enabled),
                configured_count=len(configured),
                last_error=last_error,
            ),
            auth_type=ConnectorAuthType.API_TOKEN,
            account_count=len(accounts),
            enabled_count=len(enabled),
            last_activity_at=_latest(account.updated_at for account in accounts),
            last_error=last_error,
            capabilities=("repositories", "issues", "pull_requests", "actions"),
        )

    @staticmethod
    def _feishu_item(
        accounts: Sequence[FeishuGatewayAccountRecord],
    ) -> ConnectorItem:
        enabled = tuple(
            account
            for account in accounts
            if account.status == FeishuGatewayAccountStatus.ENABLED
        )
        last_error = _first_error(account.last_error for account in accounts)
        configured = tuple(
            account for account in enabled if _feishu_secret_configured(account)
        )
        return ConnectorItem(
            connector_id="feishu",
            provider=ConnectorProvider.FEISHU,
            category=ConnectorCategory.IM,
            display_name="飞书",
            description="Connect Feishu chats and bot events to Agent Teams conversations.",
            status=_aggregate_status(
                account_count=len(accounts),
                enabled_count=len(enabled),
                configured_count=len(configured),
                last_error=last_error,
            ),
            auth_type=ConnectorAuthType.API_KEY,
            account_count=len(accounts),
            enabled_count=len(enabled),
            last_activity_at=_latest(account.updated_at for account in accounts),
            last_error=last_error,
            capabilities=("messages", "mentions", "bot_events"),
        )

    @staticmethod
    def _discord_item(
        accounts: Sequence[DiscordAccountRecord],
    ) -> ConnectorItem:
        enabled = tuple(
            account
            for account in accounts
            if account.status == DiscordAccountStatus.ENABLED
        )
        last_error = _first_error(account.last_error for account in accounts)
        configured = tuple(
            account
            for account in enabled
            if account.secret_status.bot_token_configured
        )
        return ConnectorItem(
            connector_id="discord",
            provider=ConnectorProvider.DISCORD,
            category=ConnectorCategory.IM,
            display_name="Discord",
            description="Connect Discord direct messages, mentions, and allowlisted channels.",
            status=_aggregate_status(
                account_count=len(accounts),
                enabled_count=len(enabled),
                configured_count=len(configured),
                last_error=last_error,
            ),
            auth_type=ConnectorAuthType.API_TOKEN,
            account_count=len(accounts),
            enabled_count=len(enabled),
            last_activity_at=_latest(
                _latest(
                    (
                        account.last_event_at,
                        account.last_inbound_at,
                        account.last_outbound_at,
                        account.updated_at,
                    )
                )
                for account in accounts
            ),
            last_error=last_error,
            capabilities=("direct_messages", "mentions", "group_messages"),
        )

    @staticmethod
    def _wechat_item(accounts: Sequence[WeChatAccountRecord]) -> ConnectorItem:
        enabled = tuple(
            account
            for account in accounts
            if account.status == WeChatAccountStatus.ENABLED
        )
        last_error = _first_error(account.last_error for account in accounts)
        configured = tuple(
            account for account in enabled if str(account.remote_user_id or "").strip()
        )
        return ConnectorItem(
            connector_id="wechat",
            provider=ConnectorProvider.WECHAT,
            category=ConnectorCategory.IM,
            display_name="微信",
            description="Connect WeChat direct and group conversations through QR login.",
            status=_aggregate_status(
                account_count=len(accounts),
                enabled_count=len(enabled),
                configured_count=len(configured),
                last_error=last_error,
            ),
            auth_type=ConnectorAuthType.QR_LOGIN,
            account_count=len(accounts),
            enabled_count=len(enabled),
            last_activity_at=_latest(
                _latest(
                    (
                        account.last_event_at,
                        account.last_inbound_at,
                        account.last_outbound_at,
                        account.last_login_at,
                        account.updated_at,
                    )
                )
                for account in accounts
            ),
            last_error=last_error,
            capabilities=("direct_messages", "group_messages", "file_messages"),
        )

    @staticmethod
    def _xiaoluban_item(
        accounts: Sequence[XiaolubanAccountRecord],
    ) -> ConnectorItem:
        enabled = tuple(
            account
            for account in accounts
            if account.status == XiaolubanAccountStatus.ENABLED
        )
        configured = tuple(
            account for account in enabled if _xiaoluban_im_configured(account)
        )
        return ConnectorItem(
            connector_id="xiaoluban",
            provider=ConnectorProvider.XIAOLUBAN,
            category=ConnectorCategory.IM,
            display_name="小鲁班",
            description="Connect Xiaoluban IM forwarding and notification delivery.",
            status=_aggregate_status(
                account_count=len(accounts),
                enabled_count=len(enabled),
                configured_count=len(configured),
                last_error=None,
            ),
            auth_type=ConnectorAuthType.API_TOKEN,
            account_count=len(accounts),
            enabled_count=len(enabled),
            last_activity_at=_latest(account.updated_at for account in accounts),
            last_error=None,
            capabilities=("im_forwarding", "notifications"),
        )

    async def _test_github(self) -> ConnectorTestResult:
        accounts = await self._github_trigger_service.list_accounts_async()
        item = self._github_item(accounts)
        token, missing_configured_token = await self._resolve_github_probe_token(
            accounts
        )
        if missing_configured_token:
            last_error = item.last_error or "GitHub trigger account token is missing."
            checks = (
                ConnectorHealthCheck(
                    name="account_configured",
                    ok=item.account_count > 0 and item.enabled_count > 0,
                    message="GitHub trigger account is enabled."
                    if item.enabled_count > 0
                    else "No enabled GitHub trigger account.",
                ),
                ConnectorHealthCheck(
                    name="github_connectivity",
                    ok=False,
                    message="GitHub trigger account token is missing.",
                ),
            )
            return self._test_result(
                item=item,
                ok=False,
                message="GitHub connection needs attention.",
                last_error=last_error,
                runtime_running=None,
                login_active=None,
                checks=checks,
            )
        probe_result = await self._github_connectivity_probe_service.probe_async(
            GitHubConnectivityProbeRequest(token=token, timeout_ms=15000)
        )
        last_error = item.last_error or probe_result.error_message
        checks = (
            ConnectorHealthCheck(
                name="account_configured",
                ok=item.account_count > 0 and item.enabled_count > 0,
                message="GitHub trigger account is enabled."
                if item.enabled_count > 0
                else "No enabled GitHub trigger account.",
            ),
            ConnectorHealthCheck(
                name="github_connectivity",
                ok=probe_result.ok,
                message=probe_result.username
                or probe_result.error_message
                or "GitHub probe completed.",
            ),
        )
        ok = item.status == ConnectorStatus.CONNECTED and probe_result.ok
        return self._test_result(
            item=item,
            ok=ok,
            message="GitHub connection is healthy."
            if ok
            else "GitHub connection needs attention.",
            last_error=last_error,
            runtime_running=None,
            login_active=None,
            checks=checks,
        )

    async def _resolve_github_probe_token(
        self,
        accounts: Sequence[GitHubTriggerAccountRecord],
    ) -> tuple[str | None, bool]:
        for account in accounts:
            if account.status != GitHubTriggerAccountStatus.ENABLED:
                continue
            if not account.token_configured:
                continue
            token = await self._github_trigger_service.resolve_account_token_async(
                account.account_id
            )
            if token:
                return token, False
            return None, True
        return None, False

    async def _test_feishu(self) -> ConnectorTestResult:
        accounts = await self._feishu_gateway_service.list_accounts_async()
        item = self._feishu_item(accounts)
        runtime_running = any(
            self._feishu_subscription_service.is_account_running(account.account_id)
            for account in accounts
            if account.status == FeishuGatewayAccountStatus.ENABLED
        )
        checks = (
            ConnectorHealthCheck(
                name="secret_configured",
                ok=any(_feishu_secret_configured(account) for account in accounts),
                message="Feishu account secrets are configured."
                if any(_feishu_secret_configured(account) for account in accounts)
                else "Feishu account secrets are missing.",
            ),
            ConnectorHealthCheck(
                name="subscription_running",
                ok=runtime_running,
                message="Feishu subscription runtime is running."
                if runtime_running
                else "Feishu subscription runtime is not running.",
            ),
        )
        ok = item.status == ConnectorStatus.CONNECTED and runtime_running
        return self._test_result(
            item=item,
            ok=ok,
            message="Feishu connector is healthy."
            if ok
            else "Feishu connector needs configuration or runtime attention.",
            last_error=item.last_error,
            runtime_running=runtime_running,
            login_active=None,
            checks=checks,
        )

    async def _test_discord(self) -> ConnectorTestResult:
        accounts = await self._discord_gateway_service.list_accounts()
        item = self._discord_item(accounts)
        token_configured = any(
            account.secret_status.bot_token_configured for account in accounts
        )
        running = any(
            account.running for account in accounts if account.status == DiscordAccountStatus.ENABLED
        )
        checks = (
            ConnectorHealthCheck(
                name="token_configured",
                ok=token_configured,
                message="Discord bot token is configured."
                if token_configured
                else "Discord bot token is missing.",
            ),
            ConnectorHealthCheck(
                name="worker_running",
                ok=running,
                message="Discord worker is running."
                if running
                else "Discord worker is not running.",
            ),
        )
        ok = item.status == ConnectorStatus.CONNECTED and running
        return self._test_result(
            item=item,
            ok=ok,
            message="Discord connector is healthy."
            if ok
            else "Discord connector needs a bot token, worker, or workspace attention.",
            last_error=item.last_error,
            runtime_running=running,
            login_active=None,
            checks=checks,
        )

    async def _test_wechat(self) -> ConnectorTestResult:
        accounts = await self._wechat_gateway_service.list_accounts_async()
        item = self._wechat_item(accounts)
        running = any(account.running for account in accounts)
        logged_in = any(
            str(account.remote_user_id or "").strip() for account in accounts
        )
        checks = (
            ConnectorHealthCheck(
                name="login_active",
                ok=logged_in,
                message="WeChat account has login identity."
                if logged_in
                else "WeChat account is not logged in.",
            ),
            ConnectorHealthCheck(
                name="worker_running",
                ok=running,
                message="WeChat worker is running."
                if running
                else "WeChat worker is not running.",
            ),
        )
        ok = item.status == ConnectorStatus.CONNECTED and running and logged_in
        return self._test_result(
            item=item,
            ok=ok,
            message="WeChat connector is healthy."
            if ok
            else "WeChat connector needs login or worker attention.",
            last_error=item.last_error,
            runtime_running=running,
            login_active=logged_in,
            checks=checks,
        )

    async def _test_xiaoluban(self) -> ConnectorTestResult:
        accounts = await self._xiaoluban_gateway_service.list_accounts_async()
        item = self._xiaoluban_item(accounts)
        listener_running = self._xiaoluban_im_listener_service.is_running()
        token_configured = any(
            account.secret_status.token_configured for account in accounts
        )
        im_configured = any(_xiaoluban_im_configured(account) for account in accounts)
        checks = (
            ConnectorHealthCheck(
                name="token_configured",
                ok=token_configured,
                message="Xiaoluban token is configured."
                if token_configured
                else "Xiaoluban token is missing.",
            ),
            ConnectorHealthCheck(
                name="listener_running",
                ok=listener_running,
                message="Xiaoluban IM listener is running."
                if listener_running
                else "Xiaoluban IM listener is not running.",
            ),
            ConnectorHealthCheck(
                name="im_workspace_configured",
                ok=im_configured,
                message="Xiaoluban IM workspace is configured."
                if im_configured
                else "Xiaoluban IM workspace is missing.",
            ),
        )
        ok = item.status == ConnectorStatus.CONNECTED and listener_running
        return self._test_result(
            item=item,
            ok=ok,
            message="Xiaoluban connector is healthy."
            if ok
            else "Xiaoluban connector needs token, listener, or IM workspace attention.",
            last_error=item.last_error,
            runtime_running=listener_running,
            login_active=None,
            checks=checks,
        )

    @staticmethod
    def _test_result(
        *,
        item: ConnectorItem,
        ok: bool,
        message: str,
        last_error: str | None,
        runtime_running: bool | None,
        login_active: bool | None,
        checks: tuple[ConnectorHealthCheck, ...],
    ) -> ConnectorTestResult:
        return ConnectorTestResult(
            connector_id=item.connector_id,
            provider=item.provider,
            status=item.status,
            ok=ok,
            checked_at=datetime.now(timezone.utc),
            message=message,
            account_count=item.account_count,
            enabled_count=item.enabled_count,
            runtime_running=runtime_running,
            login_active=login_active,
            last_error=last_error,
            capabilities=item.capabilities,
            checks=checks,
        )

    @staticmethod
    def _summary(items: Sequence[ConnectorItem]) -> ConnectorSummary:
        return ConnectorSummary(
            connected=sum(
                1 for item in items if item.status == ConnectorStatus.CONNECTED
            ),
            needs_config=sum(
                1 for item in items if item.status == ConnectorStatus.NEEDS_CONFIG
            ),
            disabled=sum(
                1 for item in items if item.status == ConnectorStatus.DISABLED
            ),
            error=sum(1 for item in items if item.status == ConnectorStatus.ERROR),
            total=len(items),
        )

    @staticmethod
    def _provider_for_connector_id(connector_id: str) -> ConnectorProvider:
        normalized = str(connector_id or "").strip()
        provider = CONNECTOR_PROVIDER_BY_ID.get(normalized)
        if provider is not None:
            return provider
        raise KeyError(f"Unknown connector_id: {connector_id}")


def _aggregate_status(
    *,
    account_count: int,
    enabled_count: int,
    configured_count: int,
    last_error: str | None,
) -> ConnectorStatus:
    if str(last_error or "").strip():
        return ConnectorStatus.ERROR
    if account_count == 0:
        return ConnectorStatus.NEEDS_CONFIG
    if enabled_count == 0:
        return ConnectorStatus.DISABLED
    if configured_count == 0:
        return ConnectorStatus.NEEDS_CONFIG
    return ConnectorStatus.CONNECTED


def _first_error(values: Iterable[str | None]) -> str | None:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def _latest(values: Iterable[datetime | None]) -> datetime | None:
    normalized = tuple(value for value in values if value is not None)
    if not normalized:
        return None
    return max(normalized)


def _feishu_secret_configured(account: FeishuGatewayAccountRecord) -> bool:
    status = account.secret_status or {}
    return bool(status.get("app_secret_configured"))


def _xiaoluban_im_configured(account: XiaolubanAccountRecord) -> bool:
    return bool(
        account.secret_status.token_configured
        and str(account.im_config.workspace_id or "").strip()
    )
