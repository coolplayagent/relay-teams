# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.gateway.feishu.inbound_runtime import FeishuInboundRuntime
from relay_teams.gateway.feishu.models import (
    FEISHU_METADATA_MESSAGE_ID_KEY,
    FeishuEnvironment,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from relay_teams.gateway.session_ingress_service import GatewaySessionIngressService
from relay_teams.gateway.user_questions import UserQuestionAnswerStatus
from relay_teams.providers.token_usage_repo import SessionTokenUsage
from relay_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from relay_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswerSubmission,
)
from relay_teams.sessions.session_metadata import (
    SESSION_METADATA_SOURCE_LABEL_KEY,
    SESSION_METADATA_SOURCE_PROVIDER_KEY,
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_MANUAL,
)
from relay_teams.sessions.session_models import SessionMode, SessionRecord

pytestmark = pytest.mark.asyncio


class _FakeSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.created_count = 0
        self.fail_sync_get = False

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
        if self.fail_sync_get:
            raise AssertionError("sync get_session should not be used")
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    async def get_session_async(self, session_id: str) -> SessionRecord:
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
        self.user_questions: list[dict[str, JsonValue]] = []
        self.answered_questions: list[
            tuple[str, str, UserQuestionAnswerSubmission]
        ] = []

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        return self.create_detached_run(intent)

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created.append(intent)
        return f"run-{len(self.created)}", intent.session_id

    async def create_detached_run_async(self, intent: IntentInput) -> tuple[str, str]:
        return self.create_detached_run(intent)

    def ensure_run_started(self, run_id: str) -> None:
        self.started.append(run_id)

    async def ensure_run_started_async(self, run_id: str) -> None:
        self.ensure_run_started(run_id)

    def stop_run(self, run_id: str) -> None:
        self.started.append(f"stopped:{run_id}")

    async def list_user_questions_async(
        self,
        run_id: str,
    ) -> list[dict[str, JsonValue]]:
        _ = run_id
        return self.user_questions

    async def list_user_questions_by_session_async(
        self,
        session_id: str,
    ) -> list[dict[str, JsonValue]]:
        return [
            record
            for record in self.user_questions
            if record.get("session_id") == session_id
        ]

    async def answer_user_question_async(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        self.answered_questions.append((run_id, question_id, answers))
        return {"run_id": run_id, "question_id": question_id}


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.chat_names: dict[str, str] = {}
        self.user_names: dict[str, str] = {}

    async def get_chat_name(
        self,
        *,
        chat_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = environment
        return self.chat_names.get(chat_id)

    async def get_user_name(
        self,
        *,
        open_id: str,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = environment
        return self.user_names.get(open_id)

    async def resolve_user_name(
        self,
        *,
        open_id: str,
        chat_id: str | None = None,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = (chat_id, environment)
        return self.user_names.get(open_id)


class _FakeSessionIngressService:
    def __init__(self, active_run_id: str | None = None) -> None:
        self.active_run_id = active_run_id
        self.queries: list[str] = []

    async def active_run_id_async(self, session_id: str) -> str | None:
        self.queries.append(session_id)
        return self.active_run_id


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


@pytest.mark.timeout(5)
async def test_start_run_creates_group_session_and_run(tmp_path: Path) -> None:
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

    session_id, run_id = await runtime.start_run_async(
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


async def test_resolve_session_id_uses_user_name_for_p2p(tmp_path: Path) -> None:
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

    session_id = await runtime.resolve_session_id(
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


async def test_resolve_session_id_preserves_manual_title(tmp_path: Path) -> None:
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

    session_id = await runtime.resolve_session_id(
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


async def test_answer_pending_question_without_ingress_uses_session_binding(
    tmp_path: Path,
) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    run_service.user_questions = [
        {
            "question_id": "question-1",
            "run_id": "run-detached",
            "session_id": "session-existing",
            "task_id": "task-1",
            "instance_id": "instance-1",
            "role_id": "role-1",
            "tool_name": "ask_question",
            "questions": [
                {
                    "question": "Pick a target",
                    "options": [{"label": "Ship"}, {"label": "Wait"}],
                }
            ],
            "status": "requested",
            "answers": [],
            "created_at": "2026-05-12T00:00:00+00:00",
            "updated_at": "2026-05-12T00:00:00+00:00",
            "resolved_at": None,
        }
    ]
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
    )
    session = session_service.create_session(
        session_id="session-existing",
        workspace_id="default",
    )
    session_service.fail_sync_get = True
    bindings.upsert_binding(
        platform="feishu",
        trigger_id="trg_manual",
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
        session_id=session.session_id,
    )

    status = await runtime.answer_pending_user_question_async(
        runtime_config=_build_runtime(
            trigger_id="trg_manual",
            trigger_name="manual",
            app_name="bot-manual",
        ),
        message=_build_message(
            event_id="evt-answer",
            message_id="om_answer",
            chat_id="oc_group_1",
            chat_type="group",
            trigger_text="Ship",
        ),
    )

    assert status == UserQuestionAnswerStatus.ANSWERED
    assert run_service.answered_questions[0][0] == "run-detached"
    assert run_service.answered_questions[0][1] == "question-1"


async def test_answer_pending_question_returns_not_pending_for_missing_session(
    tmp_path: Path,
) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    bindings.upsert_binding(
        platform="feishu",
        trigger_id="trg_manual",
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
        session_id="session-missing",
    )
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
    )

    status = await runtime.answer_pending_user_question_async(
        runtime_config=_build_runtime(
            trigger_id="trg_manual",
            trigger_name="manual",
            app_name="bot-manual",
        ),
        message=_build_message(
            event_id="evt-answer",
            message_id="om_answer",
            chat_id="oc_group_1",
            chat_type="group",
            trigger_text="Ship",
        ),
    )

    assert status == UserQuestionAnswerStatus.NOT_PENDING
    assert run_service.answered_questions == []


async def test_answer_pending_question_returns_not_pending_without_active_ingress_run(
    tmp_path: Path,
) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    session = session_service.create_session(
        session_id="session-existing",
        workspace_id="default",
    )
    bindings.upsert_binding(
        platform="feishu",
        trigger_id="trg_manual",
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
        session_id=session.session_id,
    )
    ingress_service = _FakeSessionIngressService()
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
        session_ingress_service=cast(GatewaySessionIngressService, ingress_service),
    )

    status = await runtime.answer_pending_user_question_async(
        runtime_config=_build_runtime(
            trigger_id="trg_manual",
            trigger_name="manual",
            app_name="bot-manual",
        ),
        message=_build_message(
            event_id="evt-answer",
            message_id="om_answer",
            chat_id="oc_group_1",
            chat_type="group",
            trigger_text="Ship",
        ),
    )

    assert status == UserQuestionAnswerStatus.NOT_PENDING
    assert ingress_service.queries == ["session-existing"]
    assert run_service.answered_questions == []


async def test_answer_pending_question_uses_active_ingress_run(
    tmp_path: Path,
) -> None:
    session_service = _FakeSessionService()
    run_service = _FakeRunService()
    run_service.user_questions = [
        {
            "question_id": "question-1",
            "run_id": "run-active",
            "session_id": "session-existing",
            "task_id": "task-1",
            "instance_id": "instance-1",
            "role_id": "role-1",
            "tool_name": "ask_question",
            "questions": [
                {
                    "question": "Pick a target",
                    "options": [{"label": "Ship"}, {"label": "Wait"}],
                }
            ],
            "status": "requested",
            "answers": [],
            "created_at": "2026-05-12T00:00:00+00:00",
            "updated_at": "2026-05-12T00:00:00+00:00",
            "resolved_at": None,
        }
    ]
    bindings = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    session = session_service.create_session(
        session_id="session-existing",
        workspace_id="default",
    )
    bindings.upsert_binding(
        platform="feishu",
        trigger_id="trg_manual",
        tenant_key="tenant-1",
        external_chat_id="oc_group_1",
        session_id=session.session_id,
    )
    ingress_service = _FakeSessionIngressService(active_run_id="run-active")
    runtime = FeishuInboundRuntime(
        session_service=session_service,
        run_service=run_service,
        external_session_binding_repo=bindings,
        session_ingress_service=cast(GatewaySessionIngressService, ingress_service),
    )

    status = await runtime.answer_pending_user_question_async(
        runtime_config=_build_runtime(
            trigger_id="trg_manual",
            trigger_name="manual",
            app_name="bot-manual",
        ),
        message=_build_message(
            event_id="evt-answer",
            message_id="om_answer",
            chat_id="oc_group_1",
            chat_type="group",
            trigger_text="Ship",
        ),
    )

    assert status == UserQuestionAnswerStatus.ANSWERED
    assert ingress_service.queries == ["session-existing"]
    assert run_service.answered_questions[0][0] == "run-active"
