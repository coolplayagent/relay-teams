# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Optional, Protocol, Tuple

from relay_teams.gateway.xiaoluban.models import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.notifications.models import NotificationRequest, NotificationType
from relay_teams.sessions.session_models import SessionRecord

LOGGER = get_logger(__name__)


class SessionLookup(Protocol):
    def get(self, session_id: str) -> SessionRecord: ...


class XiaolubanAccountLookup(Protocol):
    def list_accounts(self) -> Tuple[XiaolubanAccountRecord, ...]: ...

    def has_usable_credentials(self, account_id: str) -> bool: ...

    def send_notification_message(
        self,
        *,
        account_id: str,
        workspace_id: str,
        session_id: str,
        status: str,
        body: str,
        receiver_uid: Optional[str] = None,
    ) -> str: ...


class XiaolubanTerminalNotificationSuppressor(Protocol):
    def should_suppress_xiaoluban_terminal_notification(
        self, run_id: Optional[str]
    ) -> bool: ...


class CompositeXiaolubanTerminalNotificationSuppressor:
    def __init__(
        self,
        *suppressors: XiaolubanTerminalNotificationSuppressor | None,
    ) -> None:
        self._suppressors = tuple(
            suppressor for suppressor in suppressors if suppressor is not None
        )

    def should_suppress_xiaoluban_terminal_notification(
        self, run_id: Optional[str]
    ) -> bool:
        return any(
            suppressor.should_suppress_xiaoluban_terminal_notification(run_id)
            for suppressor in self._suppressors
        )


class XiaolubanNotificationDispatcher:
    def __init__(
        self,
        *,
        session_repo: SessionLookup,
        account_lookup: XiaolubanAccountLookup,
        terminal_notification_suppressor: Optional[
            XiaolubanTerminalNotificationSuppressor
        ] = None,
    ) -> None:
        self._session_repo = session_repo
        self._account_lookup = account_lookup
        self._terminal_notification_suppressor = terminal_notification_suppressor

    def dispatch(self, request: NotificationRequest) -> None:
        if request.notification_type != NotificationType.RUN_COMPLETED:
            return
        if (
            self._terminal_notification_suppressor is not None
            and self._terminal_notification_suppressor.should_suppress_xiaoluban_terminal_notification(
                request.context.run_id
            )
        ):
            return
        try:
            session = self._session_repo.get(request.context.session_id)
        except KeyError:
            return
        workspace_id = str(session.workspace_id or "").strip()
        if not workspace_id:
            return
        body = _build_text_payload(request)
        if not body:
            return
        for account in self._account_lookup.list_accounts():
            if not _should_notify_account(
                account=account,
                workspace_id=workspace_id,
                account_lookup=self._account_lookup,
            ):
                continue
            try:
                _ = self._account_lookup.send_notification_message(
                    account_id=account.account_id,
                    workspace_id=workspace_id,
                    session_id=request.context.session_id,
                    status="completed",
                    body=body,
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    event="gateway.xiaoluban.notification_dispatch.failed",
                    message="Failed to dispatch Xiaoluban run completion notification",
                    payload={
                        "account_id": account.account_id,
                        "workspace_id": workspace_id,
                        "run_id": request.context.run_id,
                        "session_id": request.context.session_id,
                    },
                    exc_info=exc,
                )


def _should_notify_account(
    *,
    account: XiaolubanAccountRecord,
    workspace_id: str,
    account_lookup: XiaolubanAccountLookup,
) -> bool:
    if account.status != XiaolubanAccountStatus.ENABLED:
        return False
    if workspace_id not in set(account.notification_workspace_ids):
        return False
    return account_lookup.has_usable_credentials(account.account_id)


def _build_text_payload(request: NotificationRequest) -> str:
    if request.body.strip():
        return request.body.strip()
    lines = [request.title]
    if request.context.run_id:
        lines.append(f"Run: {request.context.run_id}")
    return "\n".join(line for line in lines if line.strip())


__all__ = [
    "CompositeXiaolubanTerminalNotificationSuppressor",
    "XiaolubanNotificationDispatcher",
]
