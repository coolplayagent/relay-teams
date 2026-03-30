# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_teams.gateway.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TENANT_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FeishuEnvironment,
    FeishuMessageFormat,
    FeishuTriggerRuntimeConfig,
)
from agent_teams.notifications.models import (
    NotificationChannel,
    NotificationRequest,
    NotificationType,
)
from agent_teams.sessions.session_models import SessionRecord


class SessionLookup(Protocol):
    def get(self, session_id: str) -> SessionRecord: ...


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> FeishuTriggerRuntimeConfig | None: ...


class FeishuMessageSender(Protocol):
    def is_configured(self, environment: FeishuEnvironment | None = None) -> bool: ...

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str: ...

    def send_card_message(
        self,
        *,
        chat_id: str,
        card: dict[str, object],
        environment: FeishuEnvironment | None = None,
    ) -> str: ...


class TerminalNotificationSuppressor(Protocol):
    def should_suppress_terminal_notification(self, run_id: str | None) -> bool: ...


class CompositeTerminalNotificationSuppressor:
    def __init__(
        self,
        *suppressors: TerminalNotificationSuppressor | None,
    ) -> None:
        self._suppressors = tuple(
            suppressor for suppressor in suppressors if suppressor is not None
        )

    def should_suppress_terminal_notification(self, run_id: str | None) -> bool:
        return any(
            suppressor.should_suppress_terminal_notification(run_id)
            for suppressor in self._suppressors
        )


class FeishuNotificationDispatcher:
    def __init__(
        self,
        *,
        session_repo: SessionLookup,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        feishu_client: FeishuMessageSender,
        terminal_notification_suppressor: TerminalNotificationSuppressor | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._runtime_config_lookup = runtime_config_lookup
        self._feishu_client = feishu_client
        self._terminal_notification_suppressor = terminal_notification_suppressor

    def dispatch(self, request: NotificationRequest) -> None:
        if NotificationChannel.FEISHU not in request.channels:
            return
        if (
            request.notification_type
            in {NotificationType.RUN_COMPLETED, NotificationType.RUN_FAILED}
            and self._terminal_notification_suppressor is not None
            and self._terminal_notification_suppressor.should_suppress_terminal_notification(
                request.context.run_id
            )
        ):
            return
        session = self._session_repo.get(request.context.session_id)
        metadata = session.metadata
        if str(metadata.get(FEISHU_METADATA_PLATFORM_KEY, "")).strip() != "feishu":
            return
        trigger_id = str(metadata.get(FEISHU_METADATA_TRIGGER_ID_KEY, "")).strip()
        if not trigger_id:
            return
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            trigger_id
        )
        if runtime_config is None:
            return
        environment = runtime_config.environment
        if not self._feishu_client.is_configured(environment):
            return
        chat_id = str(metadata.get(FEISHU_METADATA_CHAT_ID_KEY, "")).strip()
        if not chat_id:
            return
        if request.feishu_format == FeishuMessageFormat.CARD:
            self._feishu_client.send_card_message(
                chat_id=chat_id,
                card=_build_card_payload(request, metadata),
                environment=environment,
            )
            return
        self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=_build_text_payload(request),
            environment=environment,
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
