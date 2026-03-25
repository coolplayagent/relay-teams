# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path

from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1
from pydantic import JsonValue

from agent_teams.feishu.models import (
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
    SESSION_METADATA_SOURCE_ICON_KEY,
    SESSION_METADATA_SOURCE_KIND_KEY,
    SESSION_METADATA_SOURCE_LABEL_KEY,
    SESSION_METADATA_SOURCE_PROVIDER_KEY,
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_SOURCE_ICON_IM,
    SESSION_SOURCE_KIND_IM,
    SESSION_TITLE_SOURCE_AUTO,
    SESSION_TITLE_SOURCE_MANUAL,
)
from agent_teams.providers.token_usage_repo import (
    SessionTokenUsage,
)
from agent_teams.feishu.trigger_handler import FeishuTriggerHandler
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from agent_teams.sessions.session_models import SessionMode, SessionRecord
from agent_teams.triggers import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerDefinition,
    TriggerEventStatus,
    TriggerIngestResult,
    TriggerSourceType,
    TriggerStatus,
)


class _FakeTriggerService:
    def __init__(self, *triggers: TriggerDefinition) -> None:
        self.triggers = {trigger.trigger_id: trigger for trigger in triggers}
        self.last_event: object | None = None

    def get_trigger(self, trigger_id: str) -> TriggerDefinition:
        try:
            return self.triggers[trigger_id]
        except KeyError as exc:
            raise KeyError(trigger_id) from exc

    def ingest_event(
        self,
        event: object,
        **_kwargs: object,
    ) -> TriggerIngestResult:
        self.last_event = event
        trigger_id = str(getattr(event, "trigger_id"))
        trigger = self.get_trigger(trigger_id)
        return TriggerIngestResult(
            accepted=True,
            event_id="tev_1",
            duplicate=False,
            status=TriggerEventStatus.RECEIVED,
            trigger_id=trigger.trigger_id,
            trigger_name=trigger.name,
        )


class _FakeFeishuConfigService:
    def __init__(
        self,
        runtime_configs: dict[str, FeishuTriggerRuntimeConfig | None],
    ) -> None:
        self.runtime_configs = runtime_configs

    def resolve_runtime_config(
        self,
        trigger: TriggerDefinition,
    ) -> FeishuTriggerRuntimeConfig | None:
        return self.runtime_configs.get(trigger.trigger_id)


class _FakeSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.created_count = 0
        self.session_messages: dict[str, list[dict[str, object]]] = {}
        self.session_token_usage: dict[str, SessionTokenUsage] = {}
        self.cleared_sessions: list[str] = []

    def create_session(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        self.created_count += 1
        now = datetime.now(tz=timezone.utc)
        resolved_session_id = session_id or f"session-{self.created_count}"
        record = SessionRecord(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            metadata={} if metadata is None else dict(metadata),
            session_mode=session_mode or SessionMode.NORMAL,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
            created_at=now,
            updated_at=now,
        )
        self.sessions[resolved_session_id] = record
        return record

    def get_session(self, session_id: str) -> SessionRecord:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        record = self.get_session(session_id)
        self.sessions[session_id] = record.model_copy(update={"metadata": metadata})

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return list(self.session_messages.get(session_id, []))

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        if session_id in self.session_token_usage:
            return self.session_token_usage[session_id]
        return SessionTokenUsage(
            session_id=session_id,
            total_input_tokens=0,
            total_cached_input_tokens=0,
            total_output_tokens=0,
            total_reasoning_output_tokens=0,
            total_tokens=0,
            total_requests=0,
            total_tool_calls=0,
            by_role={},
        )

    def clear_session_messages(self, session_id: str) -> int:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        count = len(self.session_messages.pop(session_id, []))
        self.session_token_usage.pop(session_id, None)
        self.cleared_sessions.append(session_id)
        return count


class _FakeRunService:
    def __init__(self) -> None:
        self.created: list[IntentInput] = []
        self.started: list[str] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created.append(intent)
        return f"run-{len(self.created)}", intent.session_id

    def ensure_run_started(self, run_id: str) -> None:
        self.started.append(run_id)


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.chat_names: dict[str, str] = {}
        self.user_names: dict[str, str] = {}
        self.chat_failures: set[str] = set()
        self.user_failures: set[str] = set()
        self.sent_messages: list[tuple[str, str]] = []
        self.send_failures: set[str] = set()

    def get_chat_name(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = environment
        if chat_id in self.chat_failures:
            raise RuntimeError("chat lookup failed")
        return self.chat_names.get(chat_id)

    def get_user_name(
        self,
        *,
        open_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = environment
        if open_id in self.user_failures:
            raise RuntimeError("user lookup failed")
        return self.user_names.get(open_id)

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        _ = environment
        if chat_id in self.send_failures:
            raise RuntimeError("send message failed")
        self.sent_messages.append((chat_id, text))


def _build_trigger(
    *,
    trigger_id: str,
    name: str,
    app_id: str,
    app_name: str,
    workspace_id: str = "default",
    session_mode: SessionMode = SessionMode.NORMAL,
    normal_root_role_id: str | None = None,
    orchestration_preset_id: str | None = None,
    yolo: bool = True,
    thinking: RunThinkingConfig | None = None,
) -> TriggerDefinition:
    now = datetime.now(tz=UTC)
    target_config: dict[str, JsonValue] = {
        "workspace_id": workspace_id,
        "session_mode": session_mode.value,
        "yolo": yolo,
        "thinking": ((thinking or RunThinkingConfig()).model_dump(mode="json")),
    }
    if normal_root_role_id is not None:
        target_config["normal_root_role_id"] = normal_root_role_id
    if orchestration_preset_id is not None:
        target_config["orchestration_preset_id"] = orchestration_preset_id
    return TriggerDefinition(
        trigger_id=trigger_id,
        name=name,
        display_name=name,
        source_type=TriggerSourceType.IM,
        status=TriggerStatus.ENABLED,
        public_token=f"token-{trigger_id}",
        source_config={
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": app_id,
            "app_name": app_name,
        },
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config=target_config,
        created_at=now,
        updated_at=now,
    )


def _build_runtime(
    trigger: TriggerDefinition, *, app_secret: str = "secret"
) -> FeishuTriggerRuntimeConfig:
    return FeishuTriggerRuntimeConfig(
        trigger_id=trigger.trigger_id,
        trigger_name=trigger.name,
        source=FeishuTriggerSourceConfig.model_validate(trigger.source_config),
        target=FeishuTriggerTargetConfig.model_validate(trigger.target_config or {}),
        environment=FeishuEnvironment(
            app_id=str(trigger.source_config["app_id"]),
            app_secret=app_secret,
            app_name=str(trigger.source_config["app_name"]),
        ),
    )


def _build_handler(
    *,
    tmp_path: Path,
    triggers: tuple[TriggerDefinition, ...],
    runtime_configs: dict[str, FeishuTriggerRuntimeConfig | None] | None = None,
) -> tuple[
    FeishuTriggerHandler,
    _FakeTriggerService,
    _FakeSessionService,
    _FakeRunService,
    ExternalSessionBindingRepository,
    _FakeFeishuClient,
]:
    trigger_service = _FakeTriggerService(*triggers)
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    feishu_client = _FakeFeishuClient()
    resolved_runtime_configs: dict[str, FeishuTriggerRuntimeConfig | None]
    if runtime_configs is None:
        resolved_runtime_configs = {
            trigger.trigger_id: _build_runtime(trigger) for trigger in triggers
        }
    else:
        resolved_runtime_configs = dict(runtime_configs)
    handler = FeishuTriggerHandler(
        trigger_service=trigger_service,
        feishu_config_service=_FakeFeishuConfigService(resolved_runtime_configs),
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
        feishu_client=feishu_client,
    )
    return (
        handler,
        trigger_service,
        session_service,
        run_service,
        bindings,
        feishu_client,
    )


def test_handle_sdk_event_creates_isolated_session_and_run_with_bot_preset(
    tmp_path: Path,
) -> None:
    trigger = _build_trigger(
        trigger_id="trg_feishu_ops",
        name="feishu_ops",
        app_id="cli_ops",
        app_name="ops-bot",
        workspace_id="workspace-ops",
        session_mode=SessionMode.ORCHESTRATION,
        normal_root_role_id="MainAgent",
        orchestration_preset_id="default",
        yolo=False,
        thinking=RunThinkingConfig(enabled=True, effort="high"),
    )
    (
        handler,
        _trigger_service,
        session_service,
        run_service,
        bindings,
        feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    feishu_client.chat_names["oc_group_1"] = "Repo Ops"

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-1",
        "event_type": "im.message.receive_v1",
        "tenant_key": "tenant-1"
      },
      "event": {
        "sender": {
          "sender_id": {"open_id": "ou_user"},
          "sender_type": "user"
        },
        "message": {
          "message_id": "om_1",
          "chat_id": "oc_group_1",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">ops-bot</at> please summarize this repo\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert result.session_id == "session-1"
    assert result.run_id == "run-1"
    assert session_service.sessions["session-1"].workspace_id == "workspace-ops"
    assert (
        session_service.sessions["session-1"].session_mode == SessionMode.ORCHESTRATION
    )
    assert session_service.sessions["session-1"].orchestration_preset_id == "default"
    assert getattr(_trigger_service.last_event, "event_key", None) == "om_1"
    assert len(run_service.created) == 1
    assert run_service.created[0].intent == "please summarize this repo"
    assert run_service.created[0].yolo is False
    assert run_service.created[0].thinking.enabled is True
    assert run_service.created[0].thinking.effort == "high"
    assert (
        session_service.sessions["session-1"].metadata["title"]
        == "feishu_ops · Repo Ops"
    )
    assert (
        session_service.sessions["session-1"].metadata[SESSION_METADATA_SOURCE_KIND_KEY]
        == SESSION_SOURCE_KIND_IM
    )
    assert (
        session_service.sessions["session-1"].metadata[
            SESSION_METADATA_SOURCE_PROVIDER_KEY
        ]
        == "feishu"
    )
    assert (
        session_service.sessions["session-1"].metadata[
            SESSION_METADATA_SOURCE_LABEL_KEY
        ]
        == "Repo Ops"
    )
    assert (
        session_service.sessions["session-1"].metadata[SESSION_METADATA_SOURCE_ICON_KEY]
        == SESSION_SOURCE_ICON_IM
    )
    assert (
        session_service.sessions["session-1"].metadata[
            SESSION_METADATA_TITLE_SOURCE_KEY
        ]
        == SESSION_TITLE_SOURCE_AUTO
    )
    binding = bindings.get_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
    )
    assert binding is not None
    assert binding.session_id == "session-1"


def test_handle_sdk_event_ignores_group_non_mentions(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_feishu",
        name="feishu_group",
        app_id="cli_group",
        app_name="bot",
    )
    (
        handler,
        _trigger_service,
        _session_service,
        run_service,
        _bindings,
        _feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-2",
        "event_type": "im.message.receive_v1",
        "tenant_key": "tenant-1"
      },
      "event": {
        "sender": {
          "sender_id": {"open_id": "ou_user"},
          "sender_type": "user"
        },
        "message": {
          "message_id": "om_2",
          "chat_id": "oc_group_1",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"plain message\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.ignored is True
    assert result.reason == "mention_required"
    assert run_service.created == []


def test_handle_sdk_event_accepts_p2p_without_mention(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_feishu",
        name="feishu_dm",
        app_id="cli_dm",
        app_name="bot",
    )
    (
        handler,
        _trigger_service,
        session_service,
        run_service,
        bindings,
        feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    feishu_client.user_names["ou_user"] = "Alice"

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-p2p-1",
        "event_type": "im.message.receive_v1",
        "tenant_key": "tenant-1"
      },
      "event": {
        "sender": {
          "sender_id": {"open_id": "ou_user"},
          "sender_type": "user"
        },
        "message": {
          "message_id": "om_p2p_1",
          "chat_id": "oc_p2p_1",
          "chat_type": "p2p",
          "message_type": "text",
          "content": "{\\"text\\":\\"hello from dm\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert result.session_id == "session-1"
    assert run_service.created[0].intent == "hello from dm"
    assert session_service.sessions["session-1"].metadata["feishu_chat_type"] == "p2p"
    assert (
        session_service.sessions["session-1"].metadata["title"] == "feishu_dm · Alice"
    )
    assert (
        session_service.sessions["session-1"].metadata[
            SESSION_METADATA_SOURCE_LABEL_KEY
        ]
        == "Alice"
    )
    binding = bindings.get_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_p2p_1",
    )
    assert binding is not None


def test_handle_sdk_event_isolates_same_chat_by_trigger_id(tmp_path: Path) -> None:
    trigger_a = _build_trigger(
        trigger_id="trg_bot_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
    )
    trigger_b = _build_trigger(
        trigger_id="trg_bot_b",
        name="bot_b",
        app_id="cli_b",
        app_name="bot-b",
    )
    (
        handler,
        _trigger_service,
        _session_service,
        run_service,
        bindings,
        _feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger_a, trigger_b),
    )

    raw_body_a = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-a", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_a",
          "chat_id": "oc_group_shared",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot-a</at> hello\\"}"
        }
      }
    }
    """
    raw_body_b = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-b", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_b",
          "chat_id": "oc_group_shared",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot-b</at> hello\\"}"
        }
      }
    }
    """

    result_a = handler.handle_sdk_event(
        trigger_id=trigger_a.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body_a)),
        raw_body=raw_body_a,
        headers={},
        remote_addr=None,
    )
    result_b = handler.handle_sdk_event(
        trigger_id=trigger_b.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body_b)),
        raw_body=raw_body_b,
        headers={},
        remote_addr=None,
    )

    assert result_a.session_id == "session-1"
    assert result_b.session_id == "session-2"
    assert len(run_service.created) == 2
    assert (
        bindings.get_binding(
            platform="feishu",
            trigger_id=trigger_a.trigger_id,
            tenant_key="tenant-1",
            external_chat_id="oc_group_shared",
        )
        is not None
    )
    assert (
        bindings.get_binding(
            platform="feishu",
            trigger_id=trigger_b.trigger_id,
            tenant_key="tenant-1",
            external_chat_id="oc_group_shared",
        )
        is not None
    )


def test_handle_sdk_event_ignores_trigger_without_credentials(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_missing_secret",
        name="bot",
        app_id="cli_missing",
        app_name="bot",
    )
    (
        handler,
        _trigger_service,
        _session_service,
        run_service,
        _bindings,
        _feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
        runtime_configs={trigger.trigger_id: None},
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-3", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_3",
          "chat_id": "oc_group_1",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot</at> hello\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.ignored is True
    assert result.reason == "missing_credentials"
    assert run_service.created == []


def test_handle_sdk_event_uses_fallback_title_when_feishu_name_lookup_fails(
    tmp_path: Path,
) -> None:
    trigger = _build_trigger(
        trigger_id="trg_fallback",
        name="bot_fallback",
        app_id="cli_fallback",
        app_name="bot-fallback",
    )
    (
        handler,
        _trigger_service,
        session_service,
        run_service,
        _bindings,
        feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    feishu_client.chat_failures.add("oc_group_987654321")

    raw_body = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-fallback", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_fallback",
          "chat_id": "oc_group_987654321",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot-fallback</at> hello\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert run_service.created[0].intent == "hello"
    title = session_service.sessions["session-1"].metadata["title"]
    source_label = session_service.sessions["session-1"].metadata[
        SESSION_METADATA_SOURCE_LABEL_KEY
    ]
    assert title.startswith("bot_fallback")
    assert title.endswith("Group 87654321")
    assert source_label == "Group 87654321"


def test_handle_sdk_event_preserves_manual_session_title_on_existing_binding(
    tmp_path: Path,
) -> None:
    trigger = _build_trigger(
        trigger_id="trg_manual_title",
        name="bot_manual",
        app_id="cli_manual",
        app_name="bot-manual",
    )
    (
        handler,
        _trigger_service,
        session_service,
        _run_service,
        bindings,
        feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    feishu_client.chat_names["oc_group_1"] = "Operations"
    session = session_service.create_session(
        session_id="session-existing",
        workspace_id="default",
        metadata={
            "title": "Manual Name",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
        },
    )
    bindings.upsert_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
        session_id=session.session_id,
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-manual", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_manual",
          "chat_id": "oc_group_1",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot-manual</at> hello again\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert result.session_id == "session-existing"
    updated_session = session_service.sessions["session-existing"]
    assert updated_session.metadata["title"] == "Manual Name"
    assert (
        updated_session.metadata[SESSION_METADATA_TITLE_SOURCE_KEY]
        == SESSION_TITLE_SOURCE_MANUAL
    )
    assert updated_session.metadata[SESSION_METADATA_SOURCE_LABEL_KEY] == "Operations"


def test_handle_sdk_event_sends_acknowledgement_before_run(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_ack",
        name="bot_ack",
        app_id="cli_ack",
        app_name="bot-ack",
    )
    (
        handler,
        _trigger_service,
        _session_service,
        run_service,
        _bindings,
        feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-ack", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_ack",
          "chat_id": "oc_p2p_ack",
          "chat_type": "p2p",
          "message_type": "text",
          "content": "{\\"text\\":\\"do something\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert len(run_service.created) == 1
    assert len(feishu_client.sent_messages) == 1
    chat_id, text = feishu_client.sent_messages[0]
    assert chat_id == "oc_p2p_ack"
    assert text == "收到，正在处理"


def test_handle_sdk_event_continues_when_acknowledgement_fails(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_ack_fail",
        name="bot_ack_fail",
        app_id="cli_ack_fail",
        app_name="bot-ack-fail",
    )
    (
        handler,
        _trigger_service,
        _session_service,
        run_service,
        _bindings,
        feishu_client,
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    feishu_client.send_failures.add("oc_p2p_fail")

    raw_body = """
    {
      "schema": "2.0",
      "header": {"event_id": "evt-ack-fail", "event_type": "im.message.receive_v1", "tenant_key": "tenant-1"},
      "event": {
        "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
        "message": {
          "message_id": "om_ack_fail",
          "chat_id": "oc_p2p_fail",
          "chat_type": "p2p",
          "message_type": "text",
          "content": "{\\"text\\":\\"do something\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert len(run_service.created) == 1
    assert len(feishu_client.sent_messages) == 0


# ------------------------------------------------------------------
# Session command tests
# ------------------------------------------------------------------


def _build_p2p_event(*, message_id: str, chat_id: str, event_id: str, text: str) -> str:
    return json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": event_id,
                "event_type": "im.message.receive_v1",
                "tenant_key": "tenant-1",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
                "message": {
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": text}),
                },
            },
        }
    )


def test_help_command_returns_help_and_skips_run(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_cmd",
        name="bot_cmd",
        app_id="cli_cmd",
        app_name="bot-cmd",
    )
    handler, _ts, _ss, run_service, _b, feishu_client = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    raw_body = _build_p2p_event(
        message_id="om_help",
        chat_id="oc_cmd",
        event_id="evt-help",
        text="help",
    )

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert result.reason == "session_command"
    assert run_service.created == []
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "help" in text
    assert "status" in text
    assert "clear" in text
    assert "Reset the current conversation context" in text


def test_help_command_case_insensitive(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_cmd2",
        name="bot_cmd2",
        app_id="cli_cmd2",
        app_name="bot-cmd2",
    )
    handler, _ts, _ss, run_service, _b, feishu_client = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    raw_body = _build_p2p_event(
        message_id="om_help2",
        chat_id="oc_cmd2",
        event_id="evt-help2",
        text="HELP",
    )

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert run_service.created == []
    assert len(feishu_client.sent_messages) == 1


def test_status_command_with_existing_session(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_status",
        name="bot_status",
        app_id="cli_status",
        app_name="bot-status",
    )
    handler, _ts, session_service, run_service, bindings, feishu_client = (
        _build_handler(
            tmp_path=tmp_path,
            triggers=(trigger,),
        )
    )
    session = session_service.create_session(
        session_id="session-status",
        workspace_id="default",
        metadata={},
    )
    bindings.upsert_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_status",
        session_id=session.session_id,
    )
    session_service.session_messages["session-status"] = [
        {
            "role": "user",
            "message": {"parts": [{"part_kind": "user-prompt", "content": "hello"}]},
        },
        {
            "role": "assistant",
            "message": {"parts": [{"part_kind": "text", "content": "hi there"}]},
        },
    ]
    session_service.session_token_usage["session-status"] = SessionTokenUsage(
        session_id="session-status",
        total_input_tokens=500,
        total_cached_input_tokens=100,
        total_output_tokens=200,
        total_reasoning_output_tokens=0,
        total_tokens=700,
        total_requests=3,
        total_tool_calls=1,
        by_role={},
    )

    raw_body = _build_p2p_event(
        message_id="om_status",
        chat_id="oc_status",
        event_id="evt-status",
        text="status",
    )

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert run_service.created == []
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "session-status" in text
    assert "Messages: 2" in text
    assert "500" in text
    assert "200" in text
    assert "hello" in text
    assert "hi there" in text


def test_status_command_without_session(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_status_none",
        name="bot_status_none",
        app_id="cli_status_none",
        app_name="bot-status-none",
    )
    handler, _ts, _ss, run_service, _b, feishu_client = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    raw_body = _build_p2p_event(
        message_id="om_status2",
        chat_id="oc_none",
        event_id="evt-status2",
        text="status",
    )

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert run_service.created == []
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "No active session" in text


def test_clear_command_resets_active_context(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_clear",
        name="bot_clear",
        app_id="cli_clear",
        app_name="bot-clear",
    )
    handler, _ts, session_service, run_service, bindings, feishu_client = (
        _build_handler(
            tmp_path=tmp_path,
            triggers=(trigger,),
        )
    )
    session = session_service.create_session(
        session_id="session-clear",
        workspace_id="default",
        metadata={},
    )
    bindings.upsert_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_clear",
        session_id=session.session_id,
    )
    session_service.session_messages["session-clear"] = [
        {
            "role": "user",
            "message": {"parts": [{"part_kind": "user-prompt", "content": "msg1"}]},
        },
        {
            "role": "assistant",
            "message": {"parts": [{"part_kind": "text", "content": "msg2"}]},
        },
        {
            "role": "user",
            "message": {"parts": [{"part_kind": "user-prompt", "content": "msg3"}]},
        },
    ]

    raw_body = _build_p2p_event(
        message_id="om_clear",
        chat_id="oc_clear",
        event_id="evt-clear",
        text="clear",
    )

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert run_service.created == []
    assert "session-clear" in session_service.cleared_sessions
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "Cleared 3 active messages" in text
    assert "Earlier history remains available" in text


def test_clear_command_without_session(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_clear_none",
        name="bot_clear_none",
        app_id="cli_clear_none",
        app_name="bot-clear-none",
    )
    handler, _ts, _ss, run_service, _b, feishu_client = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    raw_body = _build_p2p_event(
        message_id="om_clear2",
        chat_id="oc_none",
        event_id="evt-clear2",
        text="clear",
    )

    result = handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert run_service.created == []
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "No active session" in text


def test_command_does_not_send_acknowledgement(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_noack",
        name="bot_noack",
        app_id="cli_noack",
        app_name="bot-noack",
    )
    handler, _ts, _ss, run_service, _b, feishu_client = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )
    raw_body = _build_p2p_event(
        message_id="om_noack",
        chat_id="oc_noack",
        event_id="evt-noack",
        text="help",
    )

    handler.handle_sdk_event(
        trigger_id=trigger.trigger_id,
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert run_service.created == []
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "收到" not in text
