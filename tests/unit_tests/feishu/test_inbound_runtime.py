# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from relay_teams.gateway.feishu.inbound_runtime import FeishuInboundRuntime
from relay_teams.gateway.feishu.models import (
    FEISHU_METADATA_MESSAGE_ID_KEY,
    FeishuEnvironment,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from relay_teams.providers.token_usage_repo import SessionTokenUsage
from relay_teams.sessions import ExternalSessionBindingRepository
from relay_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from relay_teams.sessions.session_metadata import (
    SESSION_METADATA_SOURCE_LABEL_KEY,
    SESSION_METADATA_SOURCE_PROVIDER_KEY,
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_MANUAL,
)
from relay_teams.sessions.session_models import SessionMode, SessionRecord


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
        record = SessionRecord(
            session_id=session_id or f"session-{self.created_count}",
            workspace_id=workspace_id,
            metadata={} if metadata is None else dict(metadata),
            session_mode=session_mode or SessionMode.NORMAL,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
            created_at=now,
            updated_at=now,
        )
        self.sessions[record.session_id] = record
        return record

    def get_session(self, session_id: str) -> SessionRecord:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    def sync_session_metadata(self, session_id: str, metadata: dict[str, str]) -> None:
        record = self.get_session(session_id)
        self.sessions[session_id] = record.model_copy(update={"metadata": metadata})

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        _ = session_id
        return []

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
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
        _ = session_id
        return 0


class _FakeRunService:
    def __init__(self) -> None:
        self.created: list[IntentInput] = []
        self.started: list[str] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        return self.create_detached_run(intent)

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created.append(intent)
        return f"run-{len(self.created)}", intent.session_id

    def ensure_run_started(self, run_id: str) -> None:
        self.started.append(run_id)

    def stop_run(self, run_id: str) -> None:
        self.started.append(f"stopped:{run_id}")


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.chat_names: dict[str, str] = {}
        self.user_names: dict[str, str] = {}

    def get_chat_name(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = environment
        return self.chat_names.get(chat_id)

    def get_user_name(
        self,
        *,
        open_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = environment
        return self.user_names.get(open_id)

    def resolve_user_name(
        self,
        *,
        open_id: str,
        chat_id: str | None = None,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = (chat_id, environment)
        return self.user_names.get(open_id)


def _build_runtime(
    *,
    trigger_id: str,
    trigger_name: str,
    app_name: str,
    workspace_id: str = "default",
    session_mode: SessionMode = SessionMode.NORMAL,
    orchestration_preset_id: str | None = None,
    yolo: bool = True,
) -> FeishuTriggerRuntimeConfig:
    return FeishuTriggerRuntimeConfig(
        trigger_id=trigger_id,
        trigger_name=trigger_name,
        source=FeishuTriggerSourceConfig(
            provider="feishu",
            trigger_rule="mention_only",
            app_id=f"cli_{trigger_id}",
            app_name=app_name,
        ),
        target=FeishuTriggerTargetConfig(
            workspace_id=workspace_id,
            session_mode=session_mode,
            orchestration_preset_id=orchestration_preset_id,
            yolo=yolo,
            thinking=RunThinkingConfig(enabled=True, effort="high"),
        ),
        environment=FeishuEnvironment(
            app_id=f"cli_{trigger_id}",
            app_secret="secret",
            app_name=app_name,
        ),
    )


def _build_message(
    *,
    event_id: str,
    message_id: str,
    chat_id: str,
    chat_type: str,
    tenant_key: str = "tenant-1",
    sender_open_id: str | None = "ou_user",
    sender_name: str | None = None,
    trigger_text: str = "hello",
) -> FeishuNormalizedMessage:
    return FeishuNormalizedMessage(
        event_id=event_id,
        tenant_key=tenant_key,
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=message_id,
        message_type="text",
        sender_open_id=sender_open_id,
        sender_name=sender_name,
        trigger_text=trigger_text,
        payload={"message_text": trigger_text},
        metadata={"provider": "feishu", "event_id": event_id},
    )


def test_start_run_creates_group_session_and_run(tmp_path: Path) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    feishu_client = _FakeFeishuClient()
    feishu_client.chat_names["oc_group_1"] = "Repo Ops"
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
        feishu_client=feishu_client,
    )

    session_id, run_id = runtime.start_run(
        runtime_config=_build_runtime(
            trigger_id="trg_feishu",
            trigger_name="feishu_ops",
            app_name="ops-bot",
            workspace_id="workspace-ops",
            session_mode=SessionMode.ORCHESTRATION,
            orchestration_preset_id="default",
            yolo=False,
        ),
        message=_build_message(
            event_id="evt-1",
            message_id="om_1",
            chat_id="oc_group_1",
            chat_type="group",
            sender_name="Alice",
            trigger_text="please summarize this repo",
        ),
    )

    assert session_id == "session-1"
    assert run_id == "run-1"
    assert session_service.sessions["session-1"].workspace_id == "workspace-ops"
    assert (
        session_service.sessions["session-1"].metadata["title"]
        == "feishu_ops - Repo Ops"
    )
    assert (
        session_service.sessions["session-1"].metadata[
            SESSION_METADATA_SOURCE_PROVIDER_KEY
        ]
        == "feishu"
    )
    assert (
        session_service.sessions["session-1"].metadata[FEISHU_METADATA_MESSAGE_ID_KEY]
        == "om_1"
    )
    assert (
        run_service.created[0].intent
        == "收到来自 Alice 的飞书消息：please summarize this repo"
    )
    assert run_service.created[0].yolo is False
    assert run_service.created[0].conversation_context is not None
    assert run_service.created[0].conversation_context.source_provider == "feishu"
    assert run_service.created[0].conversation_context.source_kind == "im"
    assert run_service.created[0].conversation_context.feishu_chat_type == "group"


def test_resolve_session_id_uses_user_name_for_p2p(tmp_path: Path) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    feishu_client = _FakeFeishuClient()
    feishu_client.user_names["ou_user"] = "Alice"
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=ExternalSessionBindingRepository(
            tmp_path / "bindings.db"
        ),
        feishu_client=feishu_client,
    )

    session_id = runtime.resolve_session_id(
        runtime_config=_build_runtime(
            trigger_id="trg_dm",
            trigger_name="feishu_dm",
            app_name="bot",
        ),
        message=_build_message(
            event_id="evt-p2p-1",
            message_id="om_p2p_1",
            chat_id="oc_p2p_1",
            chat_type="p2p",
        ),
    )

    assert session_id == "session-1"
    assert (
        session_service.sessions["session-1"].metadata["title"] == "feishu_dm - Alice"
    )
    assert (
        session_service.sessions["session-1"].metadata[
            SESSION_METADATA_SOURCE_LABEL_KEY
        ]
        == "Alice"
    )


def test_resolve_session_id_preserves_manual_title(tmp_path: Path) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    feishu_client = _FakeFeishuClient()
    feishu_client.chat_names["oc_group_1"] = "Operations"
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
        feishu_client=feishu_client,
    )
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
        trigger_id="trg_manual",
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
        session_id=session.session_id,
    )

    session_id = runtime.resolve_session_id(
        runtime_config=_build_runtime(
            trigger_id="trg_manual",
            trigger_name="bot_manual",
            app_name="bot-manual",
        ),
        message=_build_message(
            event_id="evt-manual",
            message_id="om_manual",
            chat_id="oc_group_1",
            chat_type="group",
        ),
    )

    assert session_id == "session-existing"
    assert (
        session_service.sessions["session-existing"].metadata["title"] == "Manual Name"
    )
    assert (
        session_service.sessions["session-existing"].metadata[
            SESSION_METADATA_TITLE_SOURCE_KEY
        ]
        == SESSION_TITLE_SOURCE_MANUAL
    )
