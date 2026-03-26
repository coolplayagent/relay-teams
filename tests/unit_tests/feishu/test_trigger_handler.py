# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from typing import cast

from lark_oapi.event.dispatcher_handler import P2ImMessageReceiveV1

from agent_teams.gateway.feishu.models import (
    FeishuChatQueueClearResult,
    FeishuChatQueueItemPreview,
    FeishuChatQueueSummary,
    FeishuEnvironment,
    FeishuMessageProcessingStatus,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
    TriggerProcessingResult,
)
from agent_teams.gateway.feishu.trigger_handler import FeishuTriggerHandler
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.gateway.im import ImSessionCommandService, ImToolService
from agent_teams.providers.token_usage_repo import SessionTokenUsage
from agent_teams.sessions import ExternalSessionBindingRepository, SessionService
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from agent_teams.sessions.session_models import SessionMode, SessionRecord


class _FakeRuntimeConfigLookup:
    def __init__(self, runtime_config: FeishuTriggerRuntimeConfig | None) -> None:
        self._runtime_config = runtime_config

    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None:
        if self._runtime_config is None:
            return None
        if trigger_id != self._runtime_config.trigger_id:
            return None
        return self._runtime_config


class _FakeSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
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
        record = SessionRecord(
            session_id=session_id or "session-1",
            workspace_id=workspace_id,
            metadata={} if metadata is None else dict(metadata),
            session_mode=session_mode or SessionMode.NORMAL,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
        )
        self.sessions[record.session_id] = record
        return record

    def get_session(self, session_id: str) -> SessionRecord:
        return self.sessions[session_id]

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        self.sessions[session_id] = self.sessions[session_id].model_copy(
            update={"metadata": metadata}
        )

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return list(self.session_messages.get(session_id, []))

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        return self.session_token_usage.get(
            session_id,
            SessionTokenUsage(
                session_id=session_id,
                total_input_tokens=0,
                total_cached_input_tokens=0,
                total_output_tokens=0,
                total_reasoning_output_tokens=0,
                total_tokens=0,
                total_requests=0,
                total_tool_calls=0,
                by_role={},
            ),
        )

    def clear_session_messages(self, session_id: str) -> int:
        count = len(self.session_messages.pop(session_id, []))
        self.cleared_sessions.append(session_id)
        return count

    def get_recovery_snapshot(self, session_id: str) -> dict[str, object]:
        _ = session_id
        return {
            "active_run": {
                "run_id": "run-1",
                "status": "paused",
                "phase": "awaiting_tool_approval",
            }
        }


class _FakeRunService:
    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        _ = intent
        return "run-1", "session-1"

    def ensure_run_started(self, run_id: str) -> None:
        _ = run_id


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str]] = []

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        _ = environment
        self.sent_messages.append((chat_id, text))


class _FakeImToolService:
    def __init__(self, feishu_client: _FakeFeishuClient) -> None:
        self._feishu_client = feishu_client

    def send_text_to_feishu_chat(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=text,
            environment=environment,
        )


class _FakeMessagePoolService:
    def __init__(self) -> None:
        self.enqueued: list[FeishuNormalizedMessage] = []
        self.chat_summary = FeishuChatQueueSummary(
            trigger_id="trg_feishu",
            tenant_key="tenant-1",
            chat_id="oc_status",
            active_total=0,
        )
        self.clear_result = FeishuChatQueueClearResult(
            trigger_id="trg_feishu",
            tenant_key="tenant-1",
            chat_id="oc_status",
            cleared_queue_count=0,
            stopped_run_count=0,
        )

    def enqueue_message(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        normalized: FeishuNormalizedMessage,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        _ = (runtime_config, raw_body, headers, remote_addr)
        self.enqueued.append(normalized)
        return TriggerProcessingResult(
            status="accepted",
            trigger_id="trg_feishu",
            trigger_name="feishu_main",
            event_id=normalized.event_id,
        )

    def get_chat_summary(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
        preview_limit: int = 3,
    ) -> FeishuChatQueueSummary:
        _ = (trigger_id, tenant_key, chat_id, preview_limit)
        return self.chat_summary

    def clear_chat(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
    ) -> FeishuChatQueueClearResult:
        _ = (trigger_id, tenant_key, chat_id)
        return self.clear_result


class _FakeGatewaySessionService:
    def bind_active_run(self, gateway_session_id: str, run_id: str | None) -> None:
        _ = (gateway_session_id, run_id)


def _build_runtime(
    *,
    trigger_rule: Literal["mention_only", "all_messages"] = "mention_only",
) -> FeishuTriggerRuntimeConfig:
    return FeishuTriggerRuntimeConfig(
        trigger_id="trg_feishu",
        trigger_name="feishu_main",
        source=FeishuTriggerSourceConfig(
            provider="feishu",
            trigger_rule=trigger_rule,
            app_id="cli_demo",
            app_name="Agent Teams Bot",
        ),
        target=FeishuTriggerTargetConfig(
            workspace_id="default",
            session_mode=SessionMode.NORMAL,
            yolo=True,
            thinking=RunThinkingConfig(),
        ),
        environment=FeishuEnvironment(
            app_id="cli_demo",
            app_secret="secret-demo",
            app_name="Agent Teams Bot",
        ),
    )


def _build_handler(
    *,
    tmp_path: Path,
    runtime_config: FeishuTriggerRuntimeConfig | None = None,
) -> tuple[
    FeishuTriggerHandler,
    _FakeSessionService,
    _FakeMessagePoolService,
    ExternalSessionBindingRepository,
    _FakeFeishuClient,
]:
    session_service = _FakeSessionService()
    message_pool_service = _FakeMessagePoolService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    feishu_client = _FakeFeishuClient()
    im_tool_service = _FakeImToolService(feishu_client)
    im_session_command_service = ImSessionCommandService(
        session_service=cast(SessionService, session_service),
        run_service=cast(RunManager, _FakeRunService()),
        external_session_binding_repo=bindings,
        gateway_session_service=cast(
            GatewaySessionService,
            _FakeGatewaySessionService(),
        ),
        feishu_message_pool_service=message_pool_service,
    )
    handler = FeishuTriggerHandler(
        runtime_config_lookup=_FakeRuntimeConfigLookup(runtime_config or _build_runtime()),
        message_pool_service=message_pool_service,
        im_tool_service=cast(ImToolService, im_tool_service),
        im_session_command_service=im_session_command_service,
    )
    return handler, session_service, message_pool_service, bindings, feishu_client


def _build_event(*, message_id: str, chat_id: str, event_id: str, text: str) -> str:
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


def test_handle_sdk_event_enqueues_normal_message(tmp_path: Path) -> None:
    handler, _session_service, message_pool_service, _bindings, _feishu_client = (
        _build_handler(tmp_path=tmp_path)
    )
    raw_body = _build_event(
        message_id="om_1",
        chat_id="oc_p2p_1",
        event_id="evt-1",
        text="hello from dm",
    )

    result = handler.handle_sdk_event(
        trigger_id="trg_feishu",
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={"x-test": "1"},
        remote_addr="127.0.0.1",
    )

    assert result.status == "accepted"
    assert len(message_pool_service.enqueued) == 1
    assert message_pool_service.enqueued[0].trigger_text == "hello from dm"
    assert message_pool_service.enqueued[0].chat_id == "oc_p2p_1"


def test_help_command_returns_help_and_skips_enqueue(tmp_path: Path) -> None:
    handler, _session_service, message_pool_service, _bindings, feishu_client = (
        _build_handler(tmp_path=tmp_path)
    )
    raw_body = _build_event(
        message_id="om_help",
        chat_id="oc_cmd",
        event_id="evt-help",
        text="help",
    )

    result = handler.handle_sdk_event(
        trigger_id="trg_feishu",
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.status == "command"
    assert message_pool_service.enqueued == []
    assert len(feishu_client.sent_messages) == 1
    _, text = feishu_client.sent_messages[0]
    assert "help" in text
    assert "status" in text
    assert "clear" in text


def test_status_and_clear_commands_include_queue_state(tmp_path: Path) -> None:
    handler, session_service, message_pool_service, bindings, feishu_client = (
        _build_handler(tmp_path=tmp_path)
    )
    session = session_service.create_session(
        session_id="session-status",
        workspace_id="default",
        metadata={},
    )
    bindings.upsert_binding(
        platform="feishu",
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        external_chat_id="oc_status",
        session_id=session.session_id,
    )
    session_service.session_messages["session-status"] = [
        {
            "role": "user",
            "message": {"parts": [{"part_kind": "user-prompt", "content": "hello"}]},
        }
    ]
    session_service.session_token_usage["session-status"] = SessionTokenUsage(
        session_id="session-status",
        total_input_tokens=10,
        total_cached_input_tokens=0,
        total_output_tokens=5,
        total_reasoning_output_tokens=0,
        total_tokens=15,
        total_requests=1,
        total_tool_calls=0,
        by_role={},
    )
    message_pool_service.chat_summary = FeishuChatQueueSummary(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        chat_id="oc_status",
        active_total=2,
        waiting_result_count=1,
        queued_count=1,
        processing_item=FeishuChatQueueItemPreview(
            message_pool_id="fmp_1",
            processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
            intent_preview="first task",
            run_id="run-1",
            run_status="paused",
            run_phase="awaiting_tool_approval",
            blocking_reason="awaiting_tool_approval",
        ),
        queued_items=(
            FeishuChatQueueItemPreview(
                message_pool_id="fmp_2",
                processing_status=FeishuMessageProcessingStatus.QUEUED,
                intent_preview="second task",
            ),
        ),
    )
    message_pool_service.clear_result = FeishuChatQueueClearResult(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        chat_id="oc_status",
        cleared_queue_count=2,
        stopped_run_count=1,
    )

    status_body = _build_event(
        message_id="om_status",
        chat_id="oc_status",
        event_id="evt-status",
        text="status",
    )
    clear_body = _build_event(
        message_id="om_clear",
        chat_id="oc_status",
        event_id="evt-clear",
        text="clear",
    )

    status_result = handler.handle_sdk_event(
        trigger_id="trg_feishu",
        event=P2ImMessageReceiveV1(json.loads(status_body)),
        raw_body=status_body,
        headers={},
        remote_addr=None,
    )
    clear_result = handler.handle_sdk_event(
        trigger_id="trg_feishu",
        event=P2ImMessageReceiveV1(json.loads(clear_body)),
        raw_body=clear_body,
        headers={},
        remote_addr=None,
    )

    assert status_result.status == "command"
    assert clear_result.status == "command"
    assert message_pool_service.enqueued == []
    assert len(feishu_client.sent_messages) == 2
    assert "session-status" in feishu_client.sent_messages[0][1]
    assert "Queue: active=2 queued=1" in feishu_client.sent_messages[0][1]
    assert "blocked=awaiting_tool_approval" in feishu_client.sent_messages[0][1]
    assert (
        feishu_client.sent_messages[1][1]
        == "[Clear] Cleared 1 active session messages and 2 queued messages. Stopped 1 active runs."
    )
    assert session_service.cleared_sessions == ["session-status"]


def test_missing_runtime_config_ignores_event(tmp_path: Path) -> None:
    handler, _session_service, message_pool_service, _bindings, _feishu_client = (
        _build_handler(tmp_path=tmp_path, runtime_config=None)
    )
    raw_body = _build_event(
        message_id="om_1",
        chat_id="oc_p2p_1",
        event_id="evt-1",
        text="hello from dm",
    )

    result = handler.handle_sdk_event(
        trigger_id="missing",
        event=P2ImMessageReceiveV1(json.loads(raw_body)),
        raw_body=raw_body,
        headers={},
        remote_addr=None,
    )

    assert result.ignored is True
    assert result.reason == "missing_credentials"
    assert message_pool_service.enqueued == []
