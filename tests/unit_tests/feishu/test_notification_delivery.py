# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from agent_teams.gateway.feishu.models import (
    FEISHU_METADATA_CHAT_ID_KEY,
    FEISHU_METADATA_CHAT_TYPE_KEY,
    FEISHU_METADATA_PLATFORM_KEY,
    FEISHU_METADATA_TENANT_KEY,
    FEISHU_METADATA_TRIGGER_ID_KEY,
    FeishuEnvironment,
    FeishuMessageFormat,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from agent_teams.gateway.feishu.notification_delivery import (
    FeishuNotificationDispatcher,
)
from agent_teams.notifications import (
    NotificationChannel,
    NotificationContext,
    NotificationRequest,
    NotificationType,
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
                FEISHU_METADATA_TRIGGER_ID_KEY: "trg_feishu",
            },
            session_mode=SessionMode.NORMAL,
            created_at=now,
            updated_at=now,
        )


class _FakeRuntimeConfigLookup:
    def __init__(self, runtime_config: FeishuTriggerRuntimeConfig | None) -> None:
        self.runtime_config = runtime_config

    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> FeishuTriggerRuntimeConfig | None:
        if self.runtime_config is None:
            return None
        if self.runtime_config.trigger_id != trigger_id:
            return None
        return self.runtime_config


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, object, FeishuEnvironment | None]] = []

    def is_configured(self, environment: FeishuEnvironment | None = None) -> bool:
        return environment is not None

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        self.sent.append(("text", chat_id, text, environment))

    def send_card_message(
        self,
        *,
        chat_id: str,
        card: dict[str, object],
        environment: FeishuEnvironment | None = None,
    ) -> None:
        self.sent.append(("card", chat_id, card, environment))


class _FakeTerminalNotificationSuppressor:
    def __init__(self, *, suppress: bool) -> None:
        self.suppress = suppress

    def should_suppress_terminal_notification(self, run_id: str | None) -> bool:
        _ = run_id
        return self.suppress


def _build_runtime() -> FeishuTriggerRuntimeConfig:
    return FeishuTriggerRuntimeConfig(
        trigger_id="trg_feishu",
        trigger_name="feishu_main",
        source=FeishuTriggerSourceConfig(
            provider="feishu",
            trigger_rule="mention_only",
            app_id="cli_demo",
            app_name="bot",
        ),
        target=FeishuTriggerTargetConfig(workspace_id="default"),
        environment=FeishuEnvironment(
            app_id="cli_demo",
            app_secret="secret-demo",
            app_name="bot",
        ),
    )


def test_dispatcher_sends_text_message_with_trigger_environment() -> None:
    client = _FakeFeishuClient()
    runtime = _build_runtime()
    dispatcher = FeishuNotificationDispatcher(
        session_repo=_FakeSessionRepo(),
        runtime_config_lookup=_FakeRuntimeConfigLookup(runtime),
        feishu_client=client,
    )

    dispatcher.dispatch(
        NotificationRequest(
            notification_type=NotificationType.RUN_COMPLETED,
            title="Run Completed",
            body="ok",
            channels=(NotificationChannel.FEISHU,),
            dedupe_key="run_completed:run-1",
            context=NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
        )
    )

    assert client.sent == [("text", "chat-1", "ok", runtime.environment)]


def test_dispatcher_sends_card_message_when_requested() -> None:
    client = _FakeFeishuClient()
    runtime = _build_runtime()
    dispatcher = FeishuNotificationDispatcher(
        session_repo=_FakeSessionRepo(),
        runtime_config_lookup=_FakeRuntimeConfigLookup(runtime),
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
    kind, chat_id, payload, environment = client.sent[0]
    assert kind == "card"
    assert chat_id == "chat-1"
    assert isinstance(payload, dict)
    assert payload["header"]["title"]["content"] == "Approval Required"
    assert environment == runtime.environment


def test_dispatcher_skips_when_trigger_runtime_missing() -> None:
    client = _FakeFeishuClient()
    dispatcher = FeishuNotificationDispatcher(
        session_repo=_FakeSessionRepo(),
        runtime_config_lookup=_FakeRuntimeConfigLookup(None),
        feishu_client=client,
    )

    dispatcher.dispatch(
        NotificationRequest(
            notification_type=NotificationType.RUN_COMPLETED,
            title="Run Completed",
            body="ok",
            channels=(NotificationChannel.FEISHU,),
            dedupe_key="run_completed:run-1",
            context=NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
        )
    )

    assert client.sent == []


def test_dispatcher_skips_terminal_notifications_when_pool_owns_reply() -> None:
    client = _FakeFeishuClient()
    runtime = _build_runtime()
    dispatcher = FeishuNotificationDispatcher(
        session_repo=_FakeSessionRepo(),
        runtime_config_lookup=_FakeRuntimeConfigLookup(runtime),
        feishu_client=client,
        terminal_notification_suppressor=_FakeTerminalNotificationSuppressor(
            suppress=True
        ),
    )

    dispatcher.dispatch(
        NotificationRequest(
            notification_type=NotificationType.RUN_COMPLETED,
            title="Run Completed",
            body="ok",
            channels=(NotificationChannel.FEISHU,),
            dedupe_key="run_completed:run-1",
            context=NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
        )
    )

    assert client.sent == []
