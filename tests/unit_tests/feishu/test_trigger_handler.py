# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import UTC, datetime, timezone

from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1

from agent_teams.feishu.trigger_handler import FeishuTriggerHandler
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.runs.run_models import IntentInput
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
    def __init__(self, *, enabled: bool = True) -> None:
        now = datetime.now(tz=UTC)
        self.trigger = TriggerDefinition(
            trigger_id="trg_feishu",
            name="feishu_group",
            display_name="Feishu Group",
            source_type=TriggerSourceType.IM,
            status=TriggerStatus.ENABLED if enabled else TriggerStatus.DISABLED,
            public_token="public-token",
            source_config={"provider": "feishu", "trigger_rule": "mention_only"},
            auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
            target_config={"workspace_id": "default"},
            created_at=now,
            updated_at=now,
        )
        self.last_event: object | None = None

    def list_triggers(self) -> tuple[TriggerDefinition, ...]:
        return (self.trigger,)

    def ingest_event(
        self,
        event: object,
        **_kwargs: object,
    ) -> TriggerIngestResult:
        self.last_event = event
        return TriggerIngestResult(
            accepted=True,
            event_id="tev_1",
            duplicate=False,
            status=TriggerEventStatus.RECEIVED,
            trigger_id=self.trigger.trigger_id,
            trigger_name=self.trigger.name,
        )


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
    ) -> SessionRecord:
        self.created_count += 1
        now = datetime.now(tz=timezone.utc)
        resolved_session_id = session_id or f"session-{self.created_count}"
        record = SessionRecord(
            session_id=resolved_session_id,
            workspace_id=workspace_id,
            metadata={} if metadata is None else dict(metadata),
            session_mode=SessionMode.NORMAL,
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
        return "run-1", intent.session_id

    def ensure_run_started(self, run_id: str) -> None:
        self.started.append(run_id)


def test_handle_sdk_event_creates_session_binding_and_run(tmp_path) -> None:
    trigger_service = _FakeTriggerService()
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    handler = FeishuTriggerHandler(
        trigger_service=trigger_service,
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-1",
        "token": "verify-token",
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
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot</at> please summarize this repo\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert result.session_id == "session-1"
    assert result.run_id == "run-1"
    assert len(run_service.created) == 1
    assert run_service.created[0].intent == "please summarize this repo"
    assert run_service.created[0].yolo is True
    assert run_service.started == ["run-1"]
    binding = bindings.get_binding(
        platform="feishu",
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
    )
    assert binding is not None
    assert binding.session_id == "session-1"


def test_handle_sdk_event_ignores_non_mentions(tmp_path) -> None:
    run_service = _FakeRunService()
    handler = FeishuTriggerHandler(
        trigger_service=_FakeTriggerService(),
        session_service=_FakeSessionService(),
        run_service=run_service,
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-2",
        "token": "verify-token",
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
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.ignored is True
    assert result.reason == "mention_required"
    assert run_service.created == []


def test_handle_sdk_event_accepts_p2p_without_mention(tmp_path) -> None:
    trigger_service = _FakeTriggerService()
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    handler = FeishuTriggerHandler(
        trigger_service=trigger_service,
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-p2p-1",
        "token": "verify-token",
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
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert result.session_id == "session-1"
    assert len(run_service.created) == 1
    assert run_service.created[0].intent == "hello from dm"
    assert run_service.created[0].yolo is True
    assert session_service.sessions["session-1"].metadata["feishu_chat_type"] == "p2p"
    binding = bindings.get_binding(
        platform="feishu",
        tenant_key="tenant-1",
        external_chat_id="oc_p2p_1",
    )
    assert binding is not None
    assert binding.session_id == "session-1"


def test_handle_sdk_event_ignores_when_no_enabled_trigger(tmp_path) -> None:
    handler = FeishuTriggerHandler(
        trigger_service=_FakeTriggerService(enabled=False),
        session_service=_FakeSessionService(),
        run_service=_FakeRunService(),
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-3",
        "token": "verify-token",
        "event_type": "im.message.receive_v1",
        "tenant_key": "tenant-1"
      },
      "event": {
        "sender": {
          "sender_id": {"open_id": "ou_user"},
          "sender_type": "user"
        },
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
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.ignored is True
    assert result.reason == "no_enabled_trigger"


def test_handle_sdk_event_strips_residual_leading_mention_tokens(tmp_path) -> None:
    trigger_service = _FakeTriggerService()
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    handler = FeishuTriggerHandler(
        trigger_service=trigger_service,
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-4",
        "token": "verify-token",
        "event_type": "im.message.receive_v1",
        "tenant_key": "tenant-1"
      },
      "event": {
        "sender": {
          "sender_id": {"open_id": "ou_user"},
          "sender_type": "user"
        },
        "message": {
          "message_id": "om_4",
          "chat_id": "oc_group_1",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"@_user_1 return ok！\\"}",
          "mentions": [
            {
              "key": "@_user_1",
              "name": "bot"
            }
          ]
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert len(run_service.created) == 1
    assert run_service.created[0].intent == "return ok！"


def test_handle_sdk_event_allows_explicit_yolo_disable(tmp_path) -> None:
    trigger_service = _FakeTriggerService()
    trigger_service.trigger = trigger_service.trigger.model_copy(
        update={"target_config": {"workspace_id": "default", "yolo": False}}
    )
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    handler = FeishuTriggerHandler(
        trigger_service=trigger_service,
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
    )

    raw_body = """
    {
      "schema": "2.0",
      "header": {
        "event_id": "evt-5",
        "token": "verify-token",
        "event_type": "im.message.receive_v1",
        "tenant_key": "tenant-1"
      },
      "event": {
        "sender": {
          "sender_id": {"open_id": "ou_user"},
          "sender_type": "user"
        },
        "message": {
          "message_id": "om_5",
          "chat_id": "oc_group_1",
          "chat_type": "group",
          "message_type": "text",
          "content": "{\\"text\\":\\"<at user_id=\\\\\\"ou_bot\\\\\\">bot</at> please confirm\\"}"
        }
      }
    }
    """

    result = handler.handle_sdk_event(
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "accepted"
    assert len(run_service.created) == 1
    assert run_service.created[0].yolo is False
