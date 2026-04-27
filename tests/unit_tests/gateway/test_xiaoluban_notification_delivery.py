from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.gateway.xiaoluban import (
    CompositeXiaolubanTerminalNotificationSuppressor,
    format_xiaoluban_notification_text,
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanNotificationDispatcher,
    XiaolubanSecretStatus,
)
from relay_teams.notifications import (
    NotificationContext,
    NotificationRequest,
    NotificationType,
)
from relay_teams.sessions.session_models import SessionRecord


class _FakeSessionRepo:
    def __init__(self, sessions: tuple[SessionRecord, ...]) -> None:
        self._sessions = {session.session_id: session for session in sessions}

    def get(self, session_id: str) -> SessionRecord:
        return self._sessions[session_id]


class _FakeXiaolubanAccountLookup:
    def __init__(self, accounts: tuple[XiaolubanAccountRecord, ...]) -> None:
        self._accounts = accounts
        self.sent_messages: list[dict[str, str | None]] = []
        self.fail_send_account_ids: set[str] = set()

    def list_accounts(self) -> tuple[XiaolubanAccountRecord, ...]:
        return self._accounts

    def has_usable_credentials(self, account_id: str) -> bool:
        return any(
            account.account_id == account_id
            and account.secret_status.token_configured
            and account.status == XiaolubanAccountStatus.ENABLED
            for account in self._accounts
        )

    def send_notification_message(
        self,
        *,
        account_id: str,
        workspace_id: str,
        session_id: str,
        status: str,
        body: str,
        receiver_uid: str | None = None,
    ) -> str:
        if account_id in self.fail_send_account_ids:
            raise RuntimeError("send_failed")
        text = format_xiaoluban_notification_text(
            workspace_id=workspace_id,
            session_id=session_id,
            status=status,
            body=body,
        )
        self.sent_messages.append(
            {
                "account_id": account_id,
                "text": text,
                "receiver_uid": receiver_uid,
            }
        )
        return "xlbmsg_1"


class _Suppressor:
    def __init__(self, *, should_suppress: bool = True) -> None:
        self._should_suppress = should_suppress

    def should_suppress_xiaoluban_terminal_notification(
        self, run_id: str | None
    ) -> bool:
        _ = run_id
        return self._should_suppress


def test_composite_suppressor_delegates_to_all_members() -> None:
    suppressing = _Suppressor()
    non_suppressing = _Suppressor(should_suppress=False)
    composite = CompositeXiaolubanTerminalNotificationSuppressor(
        non_suppressing,
        suppressing,
    )

    assert composite.should_suppress_xiaoluban_terminal_notification("run-1") is True


def test_composite_suppressor_handles_none_members() -> None:
    composite = CompositeXiaolubanTerminalNotificationSuppressor(
        None,
        _Suppressor(should_suppress=False),
        None,
    )

    assert composite.should_suppress_xiaoluban_terminal_notification("run-1") is False


def test_dispatcher_sends_completed_run_to_configured_workspace_accounts() -> None:
    accounts = (
        _account("xlb_1", ("workspace-1",), "group-1"),
        _account(
            "xlb_disabled",
            ("workspace-1",),
            None,
            status=XiaolubanAccountStatus.DISABLED,
        ),
        _account("xlb_tokenless", ("workspace-1",), None, token_configured=False),
        _account("xlb_2", ("workspace-2",), None),
        _account("xlb_3", (), None),
    )
    lookup = _FakeXiaolubanAccountLookup(accounts)
    dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo((_session("session-1", "workspace-1"),)),
        account_lookup=lookup,
    )

    dispatcher.dispatch(_request(NotificationType.RUN_COMPLETED))

    assert lookup.sent_messages == [
        {
            "account_id": "xlb_1",
            "text": (
                "【relay-teams】\n"
                "session-1\n"
                "────────────────────\n"
                "Daily report is ready."
            ),
            "receiver_uid": None,
        }
    ]


def test_dispatcher_uses_title_and_run_id_when_body_is_blank() -> None:
    lookup = _FakeXiaolubanAccountLookup((_account("xlb_1", ("workspace-1",), None),))
    dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo((_session("session-1", "workspace-1"),)),
        account_lookup=lookup,
    )

    dispatcher.dispatch(
        _request(
            NotificationType.RUN_COMPLETED,
            body="   ",
            title="Run Completed",
        )
    )

    assert lookup.sent_messages == [
        {
            "account_id": "xlb_1",
            "text": (
                "【relay-teams】\n"
                "session-1\n"
                "────────────────────\n"
                "Run Completed\n"
                "Run: run-1"
            ),
            "receiver_uid": None,
        }
    ]


def test_dispatcher_skips_non_completed_and_suppressed_notifications() -> None:
    lookup = _FakeXiaolubanAccountLookup((_account("xlb_1", ("workspace-1",), None),))
    dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo((_session("session-1", "workspace-1"),)),
        account_lookup=lookup,
        terminal_notification_suppressor=_Suppressor(),
    )

    dispatcher.dispatch(_request(NotificationType.RUN_FAILED))
    dispatcher.dispatch(_request(NotificationType.RUN_COMPLETED))

    assert lookup.sent_messages == []


def test_dispatcher_skips_missing_session_blank_workspace_and_blank_payload() -> None:
    lookup = _FakeXiaolubanAccountLookup((_account("xlb_1", ("workspace-1",), None),))

    missing_session_dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo(()),
        account_lookup=lookup,
    )
    missing_session_dispatcher.dispatch(_request(NotificationType.RUN_COMPLETED))

    blank_workspace_dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo(
            (
                SessionRecord.model_construct(
                    session_id="session-1",
                    workspace_id="   ",
                ),
            )
        ),
        account_lookup=lookup,
    )
    blank_workspace_dispatcher.dispatch(_request(NotificationType.RUN_COMPLETED))

    blank_payload_dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo((_session("session-1", "workspace-1"),)),
        account_lookup=lookup,
    )
    blank_payload_dispatcher.dispatch(
        NotificationRequest.model_construct(
            notification_type=NotificationType.RUN_COMPLETED,
            title=" ",
            body=" ",
            channels=(),
            dedupe_key="run_completed:run-1",
            context=NotificationContext.model_construct(
                session_id="session-1",
                run_id="",
                trace_id="run-1",
            ),
        )
    )

    assert lookup.sent_messages == []


def test_dispatcher_logs_and_continues_when_send_fails() -> None:
    lookup = _FakeXiaolubanAccountLookup((_account("xlb_1", ("workspace-1",), None),))
    lookup.fail_send_account_ids.add("xlb_1")
    dispatcher = XiaolubanNotificationDispatcher(
        session_repo=_FakeSessionRepo((_session("session-1", "workspace-1"),)),
        account_lookup=lookup,
    )

    dispatcher.dispatch(_request(NotificationType.RUN_COMPLETED))

    assert lookup.sent_messages == []


def _session(session_id: str, workspace_id: str) -> SessionRecord:
    return SessionRecord(session_id=session_id, workspace_id=workspace_id)


def _account(
    account_id: str,
    workspace_ids: tuple[str, ...],
    receiver: str | None,
    *,
    status: XiaolubanAccountStatus = XiaolubanAccountStatus.ENABLED,
    token_configured: bool = True,
) -> XiaolubanAccountRecord:
    return XiaolubanAccountRecord(
        account_id=account_id,
        display_name=account_id,
        status=status,
        derived_uid="uidself",
        notification_workspace_ids=workspace_ids,
        notification_receiver=receiver,
        secret_status=XiaolubanSecretStatus(token_configured=token_configured),
        updated_at=datetime.now(tz=timezone.utc),
    )


def _request(
    notification_type: NotificationType,
    *,
    title: str = "Run Completed",
    body: str = "Daily report is ready.",
) -> NotificationRequest:
    return NotificationRequest(
        notification_type=notification_type,
        title=title,
        body=body,
        channels=(),
        dedupe_key="run_completed:run-1",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
        ),
    )
