from __future__ import annotations

from datetime import UTC, datetime

import pytest

from relay_teams.connector import ConnectorService, ConnectorStatus
from relay_teams.gateway.discord.models import (
    DiscordAccountRecord,
    DiscordAccountStatus,
    DiscordSecretStatus,
)
from relay_teams.gateway.feishu.models import (
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
)
from relay_teams.gateway.wechat.models import WeChatAccountRecord, WeChatAccountStatus
from relay_teams.gateway.xiaoluban import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanImConfig,
    XiaolubanSecretStatus,
)
from relay_teams.net.github_connectivity import (
    GitHubConnectivityProbeDiagnostics,
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
)
from relay_teams.triggers import (
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
)


class _GitHubService:
    def __init__(
        self,
        accounts: tuple[GitHubTriggerAccountRecord, ...],
        tokens: dict[str, str] | None = None,
    ) -> None:
        self._accounts = accounts
        self._tokens = tokens or {}

    async def list_accounts_async(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        return self._accounts

    async def resolve_account_token_async(self, account_id: str) -> str | None:
        return self._tokens.get(account_id)


class _FeishuService:
    def __init__(self, accounts: tuple[FeishuGatewayAccountRecord, ...]) -> None:
        self._accounts = accounts

    async def list_accounts_async(self) -> tuple[FeishuGatewayAccountRecord, ...]:
        return self._accounts


class _WeChatService:
    def __init__(self, accounts: tuple[WeChatAccountRecord, ...]) -> None:
        self._accounts = accounts

    async def list_accounts_async(self) -> tuple[WeChatAccountRecord, ...]:
        return self._accounts


class _DiscordService:
    def __init__(self, accounts: tuple[DiscordAccountRecord, ...]) -> None:
        self._accounts = accounts

    async def list_accounts(self) -> tuple[DiscordAccountRecord, ...]:
        return self._accounts


class _XiaolubanService:
    def __init__(self, accounts: tuple[XiaolubanAccountRecord, ...]) -> None:
        self._accounts = accounts

    async def list_accounts_async(self) -> tuple[XiaolubanAccountRecord, ...]:
        return self._accounts


class _ProbeService:
    def __init__(self) -> None:
        self.calls = 0
        self.last_request: GitHubConnectivityProbeRequest | None = None

    async def probe_async(
        self, request: GitHubConnectivityProbeRequest
    ) -> GitHubConnectivityProbeResult:
        self.calls += 1
        self.last_request = request
        return GitHubConnectivityProbeResult(
            ok=True,
            username="agent-teams-bot",
            latency_ms=10,
            checked_at=_now(),
            diagnostics=GitHubConnectivityProbeDiagnostics(
                binary_available=True,
                auth_valid=True,
                used_proxy=False,
                bundled_binary=True,
            ),
        )


class _FeishuSubscriptionService:
    def __init__(self, running_ids: tuple[str, ...] = ()) -> None:
        self._running_ids = set(running_ids)

    def is_account_running(self, account_id: str) -> bool:
        return account_id in self._running_ids


class _XiaolubanListenerService:
    def __init__(self, running: bool = True) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


@pytest.mark.asyncio
async def test_connector_summary_uses_real_builtin_providers_only() -> None:
    service = _build_service(
        github_accounts=(_github_account(),),
        discord_accounts=(_discord_account(),),
        feishu_accounts=(_feishu_account(),),
        wechat_accounts=(_wechat_account(),),
        xiaoluban_accounts=(_xiaoluban_account(),),
        feishu_running_ids=("fs_1",),
    )

    response = await service.list_connectors()

    assert [item.provider.value for item in response.items] == [
        "github",
        "discord",
        "feishu",
        "wechat",
        "xiaoluban",
    ]
    assert response.summary.total == 5
    assert response.summary.connected == 5


@pytest.mark.asyncio
async def test_empty_accounts_need_config() -> None:
    service = _build_service()

    response = await service.list_connectors()

    assert {item.connector_id: item.status for item in response.items} == {
        "github": ConnectorStatus.NEEDS_CONFIG,
        "discord": ConnectorStatus.NEEDS_CONFIG,
        "feishu": ConnectorStatus.NEEDS_CONFIG,
        "wechat": ConnectorStatus.NEEDS_CONFIG,
        "xiaoluban": ConnectorStatus.NEEDS_CONFIG,
    }
    assert response.summary.needs_config == 5


@pytest.mark.asyncio
async def test_disabled_accounts_are_reported_disabled() -> None:
    service = _build_service(
        github_accounts=(_github_account(status=GitHubTriggerAccountStatus.DISABLED),),
        discord_accounts=(_discord_account(status=DiscordAccountStatus.DISABLED),),
        feishu_accounts=(_feishu_account(status=FeishuGatewayAccountStatus.DISABLED),),
        wechat_accounts=(_wechat_account(status=WeChatAccountStatus.DISABLED),),
        xiaoluban_accounts=(
            _xiaoluban_account(status=XiaolubanAccountStatus.DISABLED),
        ),
    )

    response = await service.list_connectors()

    assert all(item.status == ConnectorStatus.DISABLED for item in response.items)
    assert response.summary.disabled == 5


@pytest.mark.asyncio
async def test_last_error_has_priority_over_connected_status() -> None:
    service = _build_service(
        github_accounts=(_github_account(last_error="webhook failed"),),
        discord_accounts=(_discord_account(last_error="worker failed"),),
        feishu_accounts=(_feishu_account(last_error="subscription failed"),),
        wechat_accounts=(_wechat_account(last_error="worker failed"),),
        xiaoluban_accounts=(_xiaoluban_account(),),
    )

    response = await service.list_connectors()
    statuses = {item.connector_id: item.status for item in response.items}

    assert statuses["github"] == ConnectorStatus.ERROR
    assert statuses["discord"] == ConnectorStatus.ERROR
    assert statuses["feishu"] == ConnectorStatus.ERROR
    assert statuses["wechat"] == ConnectorStatus.ERROR
    assert statuses["xiaoluban"] == ConnectorStatus.CONNECTED


@pytest.mark.asyncio
async def test_provider_test_results_include_runtime_checks() -> None:
    service = _build_service(
        github_accounts=(_github_account(),),
        discord_accounts=(_discord_account(),),
        feishu_accounts=(_feishu_account(),),
        wechat_accounts=(_wechat_account(),),
        xiaoluban_accounts=(_xiaoluban_account(),),
        feishu_running_ids=("fs_1",),
    )

    github = await service.test_connector("github")
    discord = await service.test_connector("discord")
    feishu = await service.test_connector("feishu")
    wechat = await service.test_connector("wechat")
    xiaoluban = await service.test_connector("xiaoluban")

    assert github.ok is True
    assert discord.runtime_running is True
    assert feishu.runtime_running is True
    assert wechat.login_active is True
    assert xiaoluban.runtime_running is True


@pytest.mark.asyncio
async def test_github_test_uses_enabled_trigger_account_token() -> None:
    account = _github_account(account_id="gh_enabled")
    probe_service = _ProbeService()
    service = _build_service(
        github_accounts=(
            _github_account(
                account_id="gh_disabled",
                status=GitHubTriggerAccountStatus.DISABLED,
            ),
            account,
        ),
        github_tokens={"gh_enabled": "ghp_account_token"},
        github_probe_service=probe_service,
    )

    result = await service.test_connector("github")

    assert result.ok is True
    assert probe_service.last_request is not None
    assert probe_service.last_request.token == "ghp_account_token"


@pytest.mark.asyncio
async def test_github_test_fails_when_configured_account_token_is_missing() -> None:
    probe_service = _ProbeService()
    service = _build_service(
        github_accounts=(_github_account(account_id="gh_missing"),),
        github_tokens={},
        github_probe_service=probe_service,
    )

    result = await service.test_connector("github")

    assert result.ok is False
    assert result.last_error == "GitHub trigger account token is missing."
    assert probe_service.calls == 0
    assert {check.name: (check.ok, check.message) for check in result.checks}[
        "github_connectivity"
    ] == (
        False,
        "GitHub trigger account token is missing.",
    )


def _build_service(
    *,
    github_accounts: tuple[GitHubTriggerAccountRecord, ...] = (),
    github_tokens: dict[str, str] | None = None,
    github_probe_service: _ProbeService | None = None,
    feishu_accounts: tuple[FeishuGatewayAccountRecord, ...] = (),
    discord_accounts: tuple[DiscordAccountRecord, ...] = (),
    wechat_accounts: tuple[WeChatAccountRecord, ...] = (),
    xiaoluban_accounts: tuple[XiaolubanAccountRecord, ...] = (),
    feishu_running_ids: tuple[str, ...] = (),
) -> ConnectorService:
    resolved_github_tokens = (
        {
            account.account_id: "ghp_test"
            for account in github_accounts
            if account.token_configured
        }
        if github_tokens is None
        else github_tokens
    )
    return ConnectorService(
        github_trigger_service=_GitHubService(github_accounts, resolved_github_tokens),
        github_connectivity_probe_service=github_probe_service or _ProbeService(),
        feishu_gateway_service=_FeishuService(feishu_accounts),
        feishu_subscription_service=_FeishuSubscriptionService(feishu_running_ids),
        discord_gateway_service=_DiscordService(discord_accounts),
        wechat_gateway_service=_WeChatService(wechat_accounts),
        xiaoluban_gateway_service=_XiaolubanService(xiaoluban_accounts),
        xiaoluban_im_listener_service=_XiaolubanListenerService(),
    )


def _github_account(
    *,
    account_id: str = "gh_1",
    status: GitHubTriggerAccountStatus = GitHubTriggerAccountStatus.ENABLED,
    last_error: str | None = None,
) -> GitHubTriggerAccountRecord:
    return GitHubTriggerAccountRecord(
        account_id=account_id,
        name="github-main",
        display_name="GitHub Main",
        status=status,
        token_configured=True,
        webhook_secret_configured=True,
        last_error=last_error,
        created_at=_now(),
        updated_at=_now(),
    )


def _feishu_account(
    *,
    status: FeishuGatewayAccountStatus = FeishuGatewayAccountStatus.ENABLED,
    last_error: str | None = None,
) -> FeishuGatewayAccountRecord:
    return FeishuGatewayAccountRecord(
        account_id="fs_1",
        name="feishu-main",
        display_name="Feishu Main",
        status=status,
        source_config={"provider": "feishu", "app_id": "cli_1", "app_name": "Bot"},
        target_config={"workspace_id": "default"},
        secret_status={"app_secret_configured": True},
        last_error=last_error,
        created_at=_now(),
        updated_at=_now(),
    )


def _wechat_account(
    *,
    status: WeChatAccountStatus = WeChatAccountStatus.ENABLED,
    last_error: str | None = None,
) -> WeChatAccountRecord:
    return WeChatAccountRecord(
        account_id="wx_1",
        display_name="WeChat Main",
        status=status,
        remote_user_id="wx-user",
        running=True,
        last_error=last_error,
        last_event_at=_now(),
    )


def _discord_account(
    *,
    status: DiscordAccountStatus = DiscordAccountStatus.ENABLED,
    last_error: str | None = None,
) -> DiscordAccountRecord:
    return DiscordAccountRecord(
        account_id="dc_1",
        display_name="Discord Main",
        status=status,
        workspace_id="default",
        secret_status=DiscordSecretStatus(bot_token_configured=True),
        running=True,
        last_error=last_error,
        last_event_at=_now(),
        updated_at=_now(),
    )


def _xiaoluban_account(
    *,
    status: XiaolubanAccountStatus = XiaolubanAccountStatus.ENABLED,
) -> XiaolubanAccountRecord:
    return XiaolubanAccountRecord(
        account_id="xlb_1",
        display_name="Xiaoluban Main",
        status=status,
        derived_uid="xlb-user",
        im_config=XiaolubanImConfig(workspace_id="default"),
        secret_status=XiaolubanSecretStatus(token_configured=True),
        created_at=_now(),
        updated_at=_now(),
    )


def _now() -> datetime:
    return datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
