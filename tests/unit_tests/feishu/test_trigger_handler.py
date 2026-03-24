# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path

from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1

from agent_teams.feishu.models import (
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
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
    def __init__(self, runtime_configs: dict[str, FeishuTriggerRuntimeConfig | None]) -> None:
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


class _FakeRunService:
    def __init__(self) -> None:
        self.created: list[IntentInput] = []
        self.started: list[str] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created.append(intent)
        return f"run-{len(self.created)}", intent.session_id

    def ensure_run_started(self, run_id: str) -> None:
        self.started.append(run_id)


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
    target_config: dict[str, object] = {
        "workspace_id": workspace_id,
        "session_mode": session_mode.value,
        "yolo": yolo,
        "thinking": (
            (thinking or RunThinkingConfig()).model_dump(mode="json")
        ),
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


def _build_runtime(trigger: TriggerDefinition, *, app_secret: str = "secret") -> FeishuTriggerRuntimeConfig:
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
]:
    trigger_service = _FakeTriggerService(*triggers)
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    resolved_runtime_configs = (
        {trigger.trigger_id: _build_runtime(trigger) for trigger in triggers}
        if runtime_configs is None
        else runtime_configs
    )
    handler = FeishuTriggerHandler(
        trigger_service=trigger_service,
        feishu_config_service=_FakeFeishuConfigService(resolved_runtime_configs),
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
    )
    return handler, trigger_service, session_service, run_service, bindings


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
    handler, _trigger_service, session_service, run_service, bindings = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )

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
    assert session_service.sessions["session-1"].session_mode == SessionMode.ORCHESTRATION
    assert session_service.sessions["session-1"].orchestration_preset_id == "default"
    assert len(run_service.created) == 1
    assert run_service.created[0].intent == "please summarize this repo"
    assert run_service.created[0].yolo is False
    assert run_service.created[0].thinking.enabled is True
    assert run_service.created[0].thinking.effort == "high"
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
    handler, _trigger_service, _session_service, run_service, _bindings = _build_handler(
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
    ) = _build_handler(
        tmp_path=tmp_path,
        triggers=(trigger,),
    )

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
    assert bindings.get_binding(
        platform="feishu",
        trigger_id=trigger_a.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_group_shared",
    ) is not None
    assert bindings.get_binding(
        platform="feishu",
        trigger_id=trigger_b.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_group_shared",
    ) is not None


def test_handle_sdk_event_ignores_trigger_without_credentials(tmp_path: Path) -> None:
    trigger = _build_trigger(
        trigger_id="trg_missing_secret",
        name="bot",
        app_id="cli_missing",
        app_name="bot",
    )
    handler, _trigger_service, _session_service, run_service, _bindings = _build_handler(
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
