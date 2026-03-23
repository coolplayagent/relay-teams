# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from agent_teams.feishu.notification_delivery import FeishuNotificationDispatcher
from agent_teams.notifications import (
    NotificationChannel,
    NotificationContext,
    NotificationRequest,
    NotificationType,
)
from agent_teams.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TENANT_KEY,
    FeishuMessageFormat,
)
from agent_teams.sessions.session_models import SessionMode, SessionRecord


class _FakeSessionRepo:
    def get(self, session_id: str) -> SessionRecord:
        _ = session_id
        now = datetime.now(tz=timezone.utc)
        return SessionRecord(
            session_id="session-1",
            workspace_id="default",
            metadata={
                FEISHU_METADATA_PLATFORM_KEY: "feishu",
                FEISHU_METADATA_TENANT_KEY: "tenant-1",
                FEISHU_METADATA_CHAT_ID_KEY: "chat-1",
                FEISHU_METADATA_CHAT_TYPE_KEY: "group",
            },
            session_mode=SessionMode.NORMAL,
            created_at=now,
            updated_at=now,
        )


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, object]] = []

    def is_configured(self) -> bool:
        return True

    def send_text_message(self, *, chat_id: str, text: str) -> None:
        self.sent.append(("text", chat_id, text))

    def send_card_message(self, *, chat_id: str, card: dict[str, object]) -> None:
        self.sent.append(("card", chat_id, card))


def test_dispatcher_sends_text_message() -> None:
    client = _FakeFeishuClient()
    dispatcher = FeishuNotificationDispatcher(
        session_repo=_FakeSessionRepo(),
        feishu_client=client,
    )

    dispatcher.dispatch(
        NotificationRequest(
            notification_type=NotificationType.RUN_COMPLETED,
            title="Run Completed",
            body="好",
            channels=(NotificationChannel.FEISHU,),
            dedupe_key="run_completed:run-1",
            context=NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
        )
    )

    assert client.sent == [
        (
            "text",
            "chat-1",
            "好",
        )
    ]


def test_dispatcher_sends_card_message_when_requested() -> None:
    client = _FakeFeishuClient()
    dispatcher = FeishuNotificationDispatcher(
        session_repo=_FakeSessionRepo(),
        feishu_client=client,
    )

    dispatcher.dispatch(
        NotificationRequest(
            notification_type=NotificationType.TOOL_APPROVAL_REQUESTED,
            title="Approval Required",
            body="spec_coder requests approval for write.",
            channels=(NotificationChannel.FEISHU,),
            feishu_format=FeishuMessageFormat.CARD,
            dedupe_key="approval:toolcall-1",
            context=NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="spec_coder",
                tool_call_id="toolcall-1",
                tool_name="write",
            ),
        )
    )

    assert len(client.sent) == 1
    kind, chat_id, payload = client.sent[0]
    assert kind == "card"
    assert chat_id == "chat-1"
    assert isinstance(payload, dict)
    assert payload["header"]["title"]["content"] == "Approval Required"
