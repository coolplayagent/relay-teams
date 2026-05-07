# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from relay_teams.gateway.gateway_models import GatewayChannelType
from relay_teams.gateway.xiaoluban.models import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    normalize_xiaoluban_notification_receivers,
)
from relay_teams.tools.notify_tools.models import (
    NotifyProvider,
    NotifyRecipient,
    NotifyRecipientKind,
    NotifyResolution,
    NotifySendAttempt,
    NotifySendResult,
    NotifyTarget,
    summarize_xiaoluban_account,
)
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.models import ToolExecutionError


def resolve_xiaoluban_notify_targets(
    ctx: ToolContext,
    *,
    account: str | None,
    target: NotifyTarget,
    recipients: tuple[str, ...],
) -> NotifyResolution:
    service = ctx.deps.xiaoluban_notify_service
    if service is None:
        raise ToolExecutionError(
            error_type="provider_unavailable",
            message="Xiaoluban notify service is not available in this runtime.",
            retryable=True,
        )
    selected_account = _resolve_account(ctx, account=account)
    normalized_recipients = normalize_xiaoluban_notification_receivers(recipients)
    resolved_recipients, filtered = _resolve_recipients(
        account=selected_account,
        target=target,
        recipients=normalized_recipients,
    )
    if not resolved_recipients:
        raise ToolExecutionError(
            error_type="empty_target",
            message="No Xiaoluban notification targets resolved.",
            details={
                "target": target.value,
                "filtered_recipients": list(filtered),
                "configured_groups": list(selected_account.notification_receivers),
            },
        )
    return NotifyResolution(
        account=selected_account,
        recipients=resolved_recipients,
        filtered_recipients=filtered,
    )


async def send_xiaoluban_notify(
    ctx: ToolContext,
    *,
    message: str,
    target: NotifyTarget,
    resolution: NotifyResolution,
) -> NotifySendResult:
    service = ctx.deps.xiaoluban_notify_service
    if service is None:
        raise ToolExecutionError(
            error_type="provider_unavailable",
            message="Xiaoluban notify service is not available in this runtime.",
            retryable=True,
        )
    body = message.strip()
    if not body:
        raise ToolExecutionError(
            error_type="validation_error",
            message="message must not be empty.",
        )
    sent: list[NotifySendAttempt] = []
    failed: list[NotifySendAttempt] = []
    for recipient in resolution.recipients:
        try:
            message_id = await service.send_notification_message(
                account_id=resolution.account.account_id,
                workspace_id=ctx.deps.workspace_id,
                session_id=ctx.deps.session_id,
                status="notification",
                body=body,
                receiver_uid=recipient.recipient_id,
            )
            sent.append(
                NotifySendAttempt(
                    recipient_id=recipient.recipient_id,
                    kind=recipient.kind,
                    ok=True,
                    message_id=message_id,
                )
            )
        except (RuntimeError, OSError, KeyError, ValueError) as exc:
            failed.append(
                NotifySendAttempt(
                    recipient_id=recipient.recipient_id,
                    kind=recipient.kind,
                    ok=False,
                    error=str(exc),
                )
            )
    if not sent:
        raise ToolExecutionError(
            error_type="delivery_failed",
            message="Xiaoluban notify failed for all resolved targets.",
            retryable=True,
            details={
                "account_id": resolution.account.account_id,
                "target": target.value,
                "failed": [_attempt_payload(attempt) for attempt in failed],
                "filtered_recipients": list(resolution.filtered_recipients),
            },
        )
    return NotifySendResult(
        provider=NotifyProvider.XIAOLUBAN,
        account=summarize_xiaoluban_account(resolution.account),
        target=target,
        sent=tuple(sent),
        failed=tuple(failed),
        filtered_recipients=resolution.filtered_recipients,
    )


def _resolve_account(
    ctx: ToolContext, *, account: str | None
) -> XiaolubanAccountRecord:
    selector = str(account or "").strip()
    if selector:
        return _resolve_explicit_account(ctx, selector=selector)
    session_account_id = _session_xiaoluban_account_id(ctx)
    if session_account_id:
        return _resolve_explicit_account(ctx, selector=session_account_id)
    usable_accounts = _usable_accounts(ctx)
    if len(usable_accounts) == 1:
        return usable_accounts[0]
    if not usable_accounts:
        raise ToolExecutionError(
            error_type="account_unavailable",
            message="No enabled Xiaoluban account with a usable token is available.",
            retryable=True,
        )
    raise ToolExecutionError(
        error_type="account_ambiguous",
        message="Multiple Xiaoluban accounts are available; specify account.",
        details={
            "candidates": [
                summarize_xiaoluban_account(item).model_dump(mode="json")
                for item in usable_accounts
            ]
        },
    )


def _resolve_explicit_account(
    ctx: ToolContext,
    *,
    selector: str,
) -> XiaolubanAccountRecord:
    service = ctx.deps.xiaoluban_notify_service
    if service is None:
        raise ToolExecutionError(
            error_type="provider_unavailable",
            message="Xiaoluban notify service is not available in this runtime.",
            retryable=True,
        )
    usable_accounts = _usable_accounts(ctx)
    for candidate in usable_accounts:
        if candidate.account_id == selector:
            return candidate
    try:
        existing = cast(XiaolubanAccountRecord, service.get_account(selector))
    except KeyError:
        existing = None
    if existing is not None:
        raise ToolExecutionError(
            error_type="account_unavailable",
            message="The selected Xiaoluban account is disabled or missing a token.",
            retryable=True,
            details={"account_id": existing.account_id},
        )
    display_matches = [
        candidate
        for candidate in usable_accounts
        if candidate.display_name.strip() == selector
    ]
    if len(display_matches) == 1:
        return display_matches[0]
    if len(display_matches) > 1:
        raise ToolExecutionError(
            error_type="account_ambiguous",
            message="Multiple usable Xiaoluban accounts have that display_name.",
            details={
                "selector": selector,
                "candidates": [
                    summarize_xiaoluban_account(item).model_dump(mode="json")
                    for item in display_matches
                ],
            },
        )
    all_display_matches = [
        candidate
        for candidate in service.list_accounts()
        if candidate.display_name.strip() == selector
    ]
    if len(all_display_matches) == 1:
        existing = cast(XiaolubanAccountRecord, all_display_matches[0])
        raise ToolExecutionError(
            error_type="account_unavailable",
            message="The selected Xiaoluban account is disabled or missing a token.",
            retryable=True,
            details={"account_id": existing.account_id},
        )
    if len(all_display_matches) > 1:
        raise ToolExecutionError(
            error_type="account_ambiguous",
            message="Multiple Xiaoluban accounts have that display_name.",
            details={
                "selector": selector,
                "candidates": [
                    summarize_xiaoluban_account(
                        cast(XiaolubanAccountRecord, item)
                    ).model_dump(mode="json")
                    for item in all_display_matches
                ],
            },
        )
    raise ToolExecutionError(
        error_type="account_not_found",
        message=f"Unknown Xiaoluban account: {selector}",
        retryable=True,
    )


def _session_xiaoluban_account_id(ctx: ToolContext) -> str:
    lookup = ctx.deps.gateway_session_lookup
    if lookup is None:
        return ""
    record = lookup.get_by_internal_session_id(ctx.deps.session_id)
    if record is None or record.channel_type != GatewayChannelType.XIAOLUBAN:
        return ""
    account_id = record.channel_state.get("account_id")
    return str(account_id or "").strip()


def _usable_accounts(ctx: ToolContext) -> tuple[XiaolubanAccountRecord, ...]:
    service = ctx.deps.xiaoluban_notify_service
    if service is None:
        return ()
    return tuple(
        cast(XiaolubanAccountRecord, account)
        for account in service.list_accounts()
        if _is_usable_account(ctx, cast(XiaolubanAccountRecord, account))
    )


def _is_usable_account(ctx: ToolContext, account: XiaolubanAccountRecord) -> bool:
    service = ctx.deps.xiaoluban_notify_service
    if service is None:
        return False
    if account.status != XiaolubanAccountStatus.ENABLED:
        return False
    if not account.secret_status.token_configured:
        return False
    return service.has_usable_credentials(account.account_id)


def _resolve_recipients(
    *,
    account: XiaolubanAccountRecord,
    target: NotifyTarget,
    recipients: tuple[str, ...],
) -> tuple[tuple[NotifyRecipient, ...], tuple[str, ...]]:
    configured_groups = account.notification_receivers
    filtered: tuple[str, ...] = ()
    if target == NotifyTarget.OWNER:
        return (
            (
                NotifyRecipient(
                    recipient_id=account.derived_uid,
                    kind=NotifyRecipientKind.OWNER,
                ),
            ),
            (),
        )
    if target == NotifyTarget.EXPLICIT:
        group_targets, filtered = _filter_configured_groups(
            configured_groups=configured_groups,
            requested=recipients,
        )
    else:
        group_targets = configured_groups

    resolved: list[NotifyRecipient] = []
    if target == NotifyTarget.OWNER_AND_CONFIGURED_GROUPS:
        resolved.append(
            NotifyRecipient(
                recipient_id=account.derived_uid,
                kind=NotifyRecipientKind.OWNER,
            )
        )
    if target in {
        NotifyTarget.CONFIGURED_GROUPS,
        NotifyTarget.OWNER_AND_CONFIGURED_GROUPS,
        NotifyTarget.EXPLICIT,
    }:
        for group_id in group_targets:
            resolved.append(
                NotifyRecipient(
                    recipient_id=group_id,
                    kind=NotifyRecipientKind.GROUP,
                )
            )
    return _deduplicate_recipients(tuple(resolved)), filtered


def _filter_configured_groups(
    *,
    configured_groups: tuple[str, ...],
    requested: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed = set(configured_groups)
    matched: list[str] = []
    filtered: list[str] = []
    seen: set[str] = set()
    for recipient in requested:
        if recipient in allowed:
            if recipient not in seen:
                seen.add(recipient)
                matched.append(recipient)
            continue
        filtered.append(recipient)
    return tuple(matched), tuple(filtered)


def _deduplicate_recipients(
    recipients: tuple[NotifyRecipient, ...],
) -> tuple[NotifyRecipient, ...]:
    seen: set[str] = set()
    result: list[NotifyRecipient] = []
    for recipient in recipients:
        key = recipient.recipient_id
        if key in seen:
            continue
        seen.add(key)
        result.append(recipient)
    return tuple(result)


def _attempt_payload(attempt: NotifySendAttempt) -> dict[str, JsonValue]:
    return attempt.model_dump(mode="json")


__all__ = [
    "resolve_xiaoluban_notify_targets",
    "send_xiaoluban_notify",
]
