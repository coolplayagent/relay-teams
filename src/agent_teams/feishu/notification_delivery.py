# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_teams.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TENANT_KEY,
    FeishuMessageFormat,
)
from agent_teams.notifications.models import (
    NotificationChannel,
    NotificationRequest,
    NotificationType,
)
from agent_teams.sessions.session_models import SessionRecord


class SessionLookup(Protocol):
    def get(self, session_id: str) -> SessionRecord: ...


class FeishuMessageSender(Protocol):
    def is_configured(self) -> bool: ...

    def send_text_message(self, *, chat_id: str, text: str) -> None: ...

    def send_card_message(self, *, chat_id: str, card: dict[str, object]) -> None: ...


class FeishuNotificationDispatcher:
    def __init__(
        self,
        *,
        session_repo: SessionLookup,
        feishu_client: FeishuMessageSender,
    ) -> None:
        self._session_repo = session_repo
        self._feishu_client = feishu_client

    def dispatch(self, request: NotificationRequest) -> None:
        if NotificationChannel.FEISHU not in request.channels:
            return
        if not self._feishu_client.is_configured():
            return
        session = self._session_repo.get(request.context.session_id)
        metadata = session.metadata
        if str(metadata.get(FEISHU_METADATA_PLATFORM_KEY, "")).strip() != "feishu":
            return
        chat_id = str(metadata.get(FEISHU_METADATA_CHAT_ID_KEY, "")).strip()
        if not chat_id:
            return
        if request.feishu_format == FeishuMessageFormat.CARD:
            self._feishu_client.send_card_message(
                chat_id=chat_id,
                card=_build_card_payload(request, metadata),
            )
            return
        self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=_build_text_payload(request),
        )


def _build_text_payload(request: NotificationRequest) -> str:
    if (
        request.notification_type == NotificationType.RUN_COMPLETED
        and request.body.strip()
    ):
        return request.body.strip()
    lines = [request.title, request.body]
    if request.context.run_id:
        lines.append(f"Run: {request.context.run_id}")
    if request.context.tool_name:
        lines.append(f"Tool: {request.context.tool_name}")
    return "\n".join(line for line in lines if line.strip())


def _build_card_payload(
    request: NotificationRequest,
    metadata: Mapping[str, str],
) -> dict[str, object]:
    fields: list[dict[str, object]] = []
    fields.append(
        {
            "is_short": False,
            "text": {
                "tag": "lark_md",
                "content": request.body,
            },
        }
    )
    tenant_key = str(metadata.get(FEISHU_METADATA_TENANT_KEY, "")).strip()
    chat_type = str(metadata.get(FEISHU_METADATA_CHAT_TYPE_KEY, "")).strip()
    if request.context.run_id:
        fields.append(
            {
                "is_short": True,
                "text": {
                    "tag": "lark_md",
                    "content": f"**Run**\n{request.context.run_id}",
                },
            }
        )
    if request.context.tool_name:
        fields.append(
            {
                "is_short": True,
                "text": {
                    "tag": "lark_md",
                    "content": f"**Tool**\n{request.context.tool_name}",
                },
            }
        )
    if request.context.role_id:
        fields.append(
            {
                "is_short": True,
                "text": {
                    "tag": "lark_md",
                    "content": f"**Role**\n{request.context.role_id}",
                },
            }
        )
    if tenant_key:
        fields.append(
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Tenant**\n{tenant_key}"},
            }
        )
    if chat_type:
        fields.append(
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Chat Type**\n{chat_type}"},
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": request.title},
        },
        "elements": [
            {
                "tag": "div",
                "fields": fields,
            }
        ],
    }
