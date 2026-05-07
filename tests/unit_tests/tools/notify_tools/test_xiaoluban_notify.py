# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.gateway import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.xiaoluban import (
    DEFAULT_XIAOLUBAN_BASE_URL,
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanSecretStatus,
)
from relay_teams.tools.notify_tools import notify as notify_module
from relay_teams.tools.notify_tools.models import (
    NotifyProvider,
    NotifyRecipientKind,
    NotifyTarget,
)
from relay_teams.tools.notify_tools.notify import build_notify_approval_request
from relay_teams.tools.notify_tools.xiaoluban import (
    resolve_xiaoluban_notify_targets,
    send_xiaoluban_notify,
)
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.models import ToolExecutionError


class _FakeXiaolubanService:
    def __init__(self, accounts: tuple[XiaolubanAccountRecord, ...]) -> None:
        self._accounts = {account.account_id: account for account in accounts}
        self.sent: list[tuple[str, str, str]] = []
        self.fail_targets: set[str] = set()

    def list_accounts(self) -> tuple[XiaolubanAccountRecord, ...]:
        return tuple(self._accounts.values())

    def get_account(self, account_id: str) -> XiaolubanAccountRecord:
        if account_id not in self._accounts:
            raise KeyError(account_id)
        return self._accounts[account_id]

    def has_usable_credentials(self, account_id: str) -> bool:
        account = self.get_account(account_id)
        return (
            account.status == XiaolubanAccountStatus.ENABLED
            and account.secret_status.token_configured
        )

    async def send_notification_message(
        self,
        *,
        account_id: str,
        workspace_id: str,
        session_id: str,
        status: str,
        body: str,
        receiver_uid: str | None = None,
    ) -> str:
        _ = (workspace_id, session_id, status, body)
        target = receiver_uid or ""
        if target in self.fail_targets:
            raise RuntimeError(f"send failed for {target}")
        self.sent.append((account_id, target, body))
        return f"msg-{len(self.sent)}"


class _FakeGatewaySessionLookup:
    def __init__(self, record: GatewaySessionRecord | None = None) -> None:
        self._record = record

    def get_by_internal_session_id(
        self,
        internal_session_id: str,
    ) -> GatewaySessionRecord | None:
        _ = internal_session_id
        return self._record


class _FakeDeps:
    def __init__(
        self,
        *,
        xiaoluban_notify_service: _FakeXiaolubanService | None,
        gateway_session_lookup: _FakeGatewaySessionLookup | None = None,
    ) -> None:
        self.xiaoluban_notify_service = xiaoluban_notify_service
        self.gateway_session_lookup = gateway_session_lookup
        self.workspace_id = "ws-1"
        self.session_id = "session-1"


class _FakeContext:
    def __init__(self, deps: _FakeDeps) -> None:
        self.deps = deps


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        _ = description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


def _account(
    account_id: str,
    *,
    display_name: str = "Xiaoluban",
    derived_uid: str = "uid_self",
    groups: tuple[str, ...] = ("group-1", "group-2"),
    enabled: bool = True,
    token_configured: bool = True,
) -> XiaolubanAccountRecord:
    now = datetime.now(tz=timezone.utc)
    return XiaolubanAccountRecord(
        account_id=account_id,
        display_name=display_name,
        base_url=DEFAULT_XIAOLUBAN_BASE_URL,
        status=(
            XiaolubanAccountStatus.ENABLED
            if enabled
            else XiaolubanAccountStatus.DISABLED
        ),
        derived_uid=derived_uid,
        notification_receivers=groups,
        secret_status=XiaolubanSecretStatus(token_configured=token_configured),
        created_at=now,
        updated_at=now,
    )


def _ctx(
    service: _FakeXiaolubanService,
    *,
    gateway_session: GatewaySessionRecord | None = None,
) -> ToolContext:
    return cast(
        ToolContext,
        _FakeContext(
            _FakeDeps(
                xiaoluban_notify_service=service,
                gateway_session_lookup=_FakeGatewaySessionLookup(gateway_session),
            )
        ),
    )


def _xiaoluban_gateway_session(account_id: str) -> GatewaySessionRecord:
    return GatewaySessionRecord(
        gateway_session_id="gws-1",
        channel_type=GatewayChannelType.XIAOLUBAN,
        external_session_id="xiaoluban:xlb:ws:remote",
        internal_session_id="session-1",
        channel_state={"account_id": account_id},
    )


@pytest.mark.asyncio
async def test_notify_register_executes_async_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))
    ctx = _ctx(service)
    fake_agent = _FakeAgent()
    notify_module.register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["notify"],
    )

    async def _fake_execute_tool(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        tool_input: dict[str, JsonValue],
        action: Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]],
        **kwargs: object,
    ) -> dict[str, JsonValue]:
        _ = (ctx, tool_name, args_summary, kwargs)
        return await action(tool_input)

    monkeypatch.setattr(notify_module, "execute_tool", _fake_execute_tool)

    result = await tool(
        ctx,
        provider=NotifyProvider.XIAOLUBAN,
        message="hello",
        target=NotifyTarget.OWNER,
    )

    sent = cast(list[dict[str, JsonValue]], result["sent"])
    assert sent[0]["message_id"] == "msg-1"
    assert service.sent == [("xlb_1", "uid_self", "hello")]


def test_default_owner_target_uses_single_available_account_owner() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    resolution = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account=None,
        target=NotifyTarget.OWNER,
        recipients=(),
    )

    assert resolution.account.account_id == "xlb_1"
    assert resolution.recipients[0].recipient_id == "uid_self"
    assert resolution.recipients[0].kind == NotifyRecipientKind.OWNER
    assert resolution.includes_group is False


def test_session_xiaoluban_account_takes_precedence() -> None:
    service = _FakeXiaolubanService(
        (
            _account("xlb_session", display_name="Session"),
            _account("xlb_other", display_name="Other"),
        )
    )

    resolution = resolve_xiaoluban_notify_targets(
        _ctx(service, gateway_session=_xiaoluban_gateway_session("xlb_session")),
        account=None,
        target=NotifyTarget.OWNER,
        recipients=(),
    )

    assert resolution.account.account_id == "xlb_session"


def test_multiple_accounts_without_selector_fail_with_candidates() -> None:
    service = _FakeXiaolubanService(
        (
            _account("xlb_1", display_name="One"),
            _account("xlb_2", display_name="Two"),
        )
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        _ = resolve_xiaoluban_notify_targets(
            _ctx(service),
            account=None,
            target=NotifyTarget.OWNER,
            recipients=(),
        )

    assert exc_info.value.error_type == "account_ambiguous"
    assert len(cast(list[JsonValue], exc_info.value.details["candidates"])) == 2


def test_account_selector_matches_unique_display_name() -> None:
    service = _FakeXiaolubanService(
        (
            _account("xlb_1", display_name="Main"),
            _account("xlb_2", display_name="Other"),
        )
    )

    resolution = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account="Main",
        target=NotifyTarget.OWNER,
        recipients=(),
    )

    assert resolution.account.account_id == "xlb_1"


def test_account_selector_prioritizes_account_id_over_display_name() -> None:
    service = _FakeXiaolubanService(
        (
            _account("shared", display_name="Disabled", enabled=False),
            _account("xlb_2", display_name="shared"),
        )
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        _ = resolve_xiaoluban_notify_targets(
            _ctx(service),
            account="shared",
            target=NotifyTarget.OWNER,
            recipients=(),
        )

    assert exc_info.value.error_type == "account_unavailable"
    assert exc_info.value.details["account_id"] == "shared"


def test_configured_groups_and_owner_plus_groups_resolve_whitelist() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    groups_only = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account=None,
        target=NotifyTarget.CONFIGURED_GROUPS,
        recipients=(),
    )
    owner_and_groups = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account=None,
        target=NotifyTarget.OWNER_AND_CONFIGURED_GROUPS,
        recipients=(),
    )

    assert [recipient.recipient_id for recipient in groups_only.recipients] == [
        "group-1",
        "group-2",
    ]
    assert [recipient.recipient_id for recipient in owner_and_groups.recipients] == [
        "uid_self",
        "group-1",
        "group-2",
    ]


def test_non_explicit_group_targets_ignore_incidental_recipients() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    groups_only = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account=None,
        target=NotifyTarget.CONFIGURED_GROUPS,
        recipients=("not-allowed", "group-2"),
    )
    owner_and_groups = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account=None,
        target=NotifyTarget.OWNER_AND_CONFIGURED_GROUPS,
        recipients=("not-allowed",),
    )

    assert [recipient.recipient_id for recipient in groups_only.recipients] == [
        "group-1",
        "group-2",
    ]
    assert groups_only.filtered_recipients == ()
    assert [recipient.recipient_id for recipient in owner_and_groups.recipients] == [
        "uid_self",
        "group-1",
        "group-2",
    ]
    assert owner_and_groups.filtered_recipients == ()


@pytest.mark.asyncio
async def test_owner_and_group_targets_deduplicate_by_recipient_id() -> None:
    service = _FakeXiaolubanService(
        (_account("xlb_1", groups=("uid_self", "group-1")),)
    )
    ctx = _ctx(service)

    resolution = resolve_xiaoluban_notify_targets(
        ctx,
        account=None,
        target=NotifyTarget.OWNER_AND_CONFIGURED_GROUPS,
        recipients=(),
    )
    result = await send_xiaoluban_notify(
        ctx,
        message="hello",
        target=NotifyTarget.OWNER_AND_CONFIGURED_GROUPS,
        resolution=resolution,
    )

    assert [recipient.recipient_id for recipient in resolution.recipients] == [
        "uid_self",
        "group-1",
    ]
    assert [attempt.recipient_id for attempt in result.sent] == [
        "uid_self",
        "group-1",
    ]
    assert [sent[1] for sent in service.sent] == ["uid_self", "group-1"]


def test_explicit_recipients_are_filtered_to_configured_groups() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    resolution = resolve_xiaoluban_notify_targets(
        _ctx(service),
        account=None,
        target=NotifyTarget.EXPLICIT,
        recipients=("group-2", "not-allowed"),
    )

    assert [recipient.recipient_id for recipient in resolution.recipients] == [
        "group-2"
    ]
    assert resolution.filtered_recipients == ("not-allowed",)


def test_explicit_recipients_fail_when_all_filtered() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    with pytest.raises(ToolExecutionError) as exc_info:
        _ = resolve_xiaoluban_notify_targets(
            _ctx(service),
            account=None,
            target=NotifyTarget.EXPLICIT,
            recipients=("not-allowed",),
        )

    assert exc_info.value.error_type == "empty_target"
    assert exc_info.value.details["filtered_recipients"] == ["not-allowed"]


def test_owner_only_notify_does_not_build_approval_request() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    request = build_notify_approval_request(
        _ctx(service),
        {
            "provider": "xiaoluban",
            "message": "hello",
            "target": "owner",
            "recipients": [],
        },
    )

    assert request is None


def test_group_notify_builds_guarded_approval_request() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    request = build_notify_approval_request(
        _ctx(service),
        {
            "provider": "xiaoluban",
            "message": "hello",
            "target": "configured_groups",
            "recipients": [],
        },
    )

    assert request is not None
    assert request.risk_level is not None
    assert request.risk_level.value == "guarded"
    assert request.metadata["group_target_count"] == 2
    assert "2 group target" in request.target_summary


@pytest.mark.asyncio
async def test_send_reports_partial_failure_and_filtered_recipients() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))
    service.fail_targets.add("group-2")
    ctx = _ctx(service)
    resolution = resolve_xiaoluban_notify_targets(
        ctx,
        account=None,
        target=NotifyTarget.EXPLICIT,
        recipients=("group-1", "group-2", "not-allowed"),
    )

    result = await send_xiaoluban_notify(
        ctx,
        message="hello",
        target=NotifyTarget.EXPLICIT,
        resolution=resolution,
    )

    assert [attempt.recipient_id for attempt in result.sent] == ["group-1"]
    assert [attempt.recipient_id for attempt in result.failed] == ["group-2"]
    assert result.filtered_recipients == ("not-allowed",)


@pytest.mark.asyncio
async def test_send_fails_when_all_targets_fail() -> None:
    service = _FakeXiaolubanService((_account("xlb_1", groups=("group-1",)),))
    service.fail_targets.add("group-1")
    ctx = _ctx(service)
    resolution = resolve_xiaoluban_notify_targets(
        ctx,
        account=None,
        target=NotifyTarget.CONFIGURED_GROUPS,
        recipients=(),
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        _ = await send_xiaoluban_notify(
            ctx,
            message="hello",
            target=NotifyTarget.CONFIGURED_GROUPS,
            resolution=resolution,
        )

    assert exc_info.value.error_type == "delivery_failed"


@pytest.mark.asyncio
async def test_notify_action_executes_xiaoluban_send() -> None:
    service = _FakeXiaolubanService((_account("xlb_1"),))

    payload = await notify_module._execute_notify_action(
        _ctx(service),
        {
            "provider": "xiaoluban",
            "message": " hello ",
            "target": "owner",
            "recipients": [],
        },
    )

    assert payload["provider"] == "xiaoluban"
    assert payload["target"] == "owner"
    sent = cast(list[dict[str, JsonValue]], payload["sent"])
    assert sent[0]["recipient_id"] == "uid_self"
    assert service.sent == [("xlb_1", "uid_self", "hello")]


def test_notify_input_helpers_normalize_values() -> None:
    assert notify_module._optional_text(None) is None
    assert notify_module._optional_text("  ") is None
    assert notify_module._optional_text(" owner ") == "owner"
    assert notify_module._optional_text(123) == "123"
    assert notify_module._recipient_tuple(None) == ()
    assert notify_module._recipient_tuple(["a", 1]) == ("a", "1")
    assert notify_module._recipient_tuple(cast(JsonValue, ("b", 2))) == ("b", "2")
    assert notify_module._recipient_tuple("c") == ("c",)
    assert notify_module._recipient_tuple(3) == ("3",)


def test_notify_approval_args_summary_counts_recipients() -> None:
    summary = notify_module._approval_args_summary(
        {
            "provider": "xiaoluban",
            "target": "explicit",
            "account": " xlb_1 ",
            "recipients": ["group-1", "group-2"],
        }
    )

    assert summary == {
        "provider": "xiaoluban",
        "target": "explicit",
        "account": "xlb_1",
        "recipient_count": 2,
    }
