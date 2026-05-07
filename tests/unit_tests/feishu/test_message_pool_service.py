# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from datetime import datetime, timedelta, timezone
from pathlib import Path

from relay_teams.gateway.feishu.inbound_runtime import FeishuInboundRuntime
from relay_teams.gateway.feishu.message_pool_repository import (
    FeishuMessagePoolRepository,
)
from relay_teams.gateway.feishu.message_pool_service import (
    FeishuMessagePoolService,
    _build_pause_reply,
    _build_queue_reply_text,
)
from relay_teams.gateway.feishu.models import (
    FeishuEnvironment,
    FeishuMessageDeliveryStatus,
    FeishuMessageProcessingStatus,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from relay_teams.media import content_parts_from_text
from relay_teams.providers.token_usage_repo import SessionTokenUsage
from relay_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import IntentInput, RunEvent, RunResult
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import SessionMode, SessionRecord
from relay_teams.automation.automation_bound_session_queue_repository import (
    AutomationBoundSessionQueueRepository,
)

pytestmark = pytest.mark.asyncio


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
        self.stopped: list[str] = []
        self.fail_start_error: RuntimeError | None = None

    def create_run(self, intent: IntentInput) -> tuple[str, str]:
        return self.create_detached_run(intent)

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created.append(intent)
        return f"run-{len(self.created)}", intent.session_id

    async def create_detached_run_async(self, intent: IntentInput) -> tuple[str, str]:
        return self.create_detached_run(intent)

    def ensure_run_started(self, run_id: str) -> None:
        if self.fail_start_error is not None:
            raise self.fail_start_error
        self.started.append(run_id)

    async def ensure_run_started_async(self, run_id: str) -> None:
        self.ensure_run_started(run_id)

    def stop_run(self, run_id: str) -> None:
        self.stopped.append(run_id)


class _FakeRuntimeConfigLookup:
    def __init__(self, runtime_config: FeishuTriggerRuntimeConfig) -> None:
        self.runtime_config = runtime_config

    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None:
        if trigger_id != self.runtime_config.trigger_id:
            return None
        return self.runtime_config


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str]] = []
        self.reply_messages: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []
        self.user_names: dict[str, str] = {}

    async def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        _ = environment
        self.sent_messages.append((chat_id, text))
        return f"om_{len(self.sent_messages)}"

    async def reply_text_message(
        self,
        *,
        message_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str:
        _ = environment
        self.reply_messages.append((message_id, text))
        return f"om_reply_{len(self.reply_messages)}"

    async def create_message_reaction(
        self,
        *,
        message_id: str,
        reaction_type: str,
        environment: FeishuEnvironment | None = None,
    ) -> None:
        _ = environment
        self.reactions.append((message_id, reaction_type))

    async def resolve_user_name(
        self,
        *,
        open_id: str,
        chat_id: str | None = None,
        environment: FeishuEnvironment | None = None,
    ) -> str | None:
        _ = (chat_id, environment)
        return self.user_names.get(open_id)


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


def _build_message(
    *,
    event_id: str,
    message_id: str,
    text: str,
    chat_id: str = "oc_group_1",
    chat_type: str = "group",
    sender_open_id: str | None = None,
) -> FeishuNormalizedMessage:
    return FeishuNormalizedMessage(
        event_id=event_id,
        tenant_key="tenant-1",
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=message_id,
        message_type="text",
        trigger_text=text,
        payload={"raw_text": text, "message_text": text},
        metadata={"provider": "feishu", "message_id": message_id},
        sender_open_id=sender_open_id,
    )


def _build_service(
    tmp_path: Path,
) -> tuple[
    FeishuMessagePoolService,
    FeishuMessagePoolRepository,
    _FakeFeishuClient,
    RunRuntimeRepository,
    EventLog,
    _FakeRunService,
]:
    runtime = _build_runtime()
    db_path = tmp_path / "feishu-pool.db"
    repo = FeishuMessagePoolRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    bindings = ExternalSessionBindingRepository(db_path)
    feishu_client = _FakeFeishuClient()
    run_service = _FakeRunService()
    inbound_runtime = FeishuInboundRuntime(
        session_service=_FakeSessionService(),
        run_service=run_service,
        external_session_binding_repo=bindings,
        feishu_client=None,
    )
    service = FeishuMessagePoolService(
        runtime_config_lookup=_FakeRuntimeConfigLookup(runtime),
        inbound_runtime=inbound_runtime,
        feishu_client=feishu_client,
        message_pool_repo=repo,
        run_runtime_repo=run_runtime_repo,
        event_log=event_log,
        external_session_binding_repo=bindings,
        automation_queue_repo=AutomationBoundSessionQueueRepository(db_path),
    )
    return service, repo, feishu_client, run_runtime_repo, event_log, run_service


async def test_enqueue_message_uses_queue_aware_ack(tmp_path: Path) -> None:
    service, repo, feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )
    runtime = _build_runtime()

    first = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="first"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    second = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-2", message_id="om_2", text="second"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    assert first.status == "accepted"
    assert second.status == "accepted"
    _ = await service._retry_pending_reactions()
    _ = await service._retry_pending_queue_replies()
    assert feishu_client.reactions == [("om_1", "OK"), ("om_2", "OK")]
    assert feishu_client.reply_messages == [("om_2", _build_queue_reply_text(1))]
    assert feishu_client.sent_messages == []
    first_record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    second_record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_2",
    )
    assert first_record.reaction_status == FeishuMessageDeliveryStatus.SENT
    assert first_record.ack_status == FeishuMessageDeliveryStatus.SKIPPED
    assert second_record.ack_status == FeishuMessageDeliveryStatus.SENT
    assert second_record.reaction_status == FeishuMessageDeliveryStatus.SENT


async def test_wake_signal_is_thread_safe(tmp_path: Path) -> None:
    service, _repo, _feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )

    service._loop = asyncio.get_running_loop()
    service._wake_event.clear()
    await asyncio.to_thread(service._wake)
    await asyncio.wait_for(service._wake_event.wait(), timeout=1)
    service._loop = None


async def test_wake_signal_sets_event_without_running_loop(tmp_path: Path) -> None:
    service, _repo, _feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )

    service._wake_event.clear()
    service._wake()

    assert service._wake_event.is_set()


async def test_start_reuses_running_task_and_stop_clears_loop(tmp_path: Path) -> None:
    service, _repo, _feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )

    await service.start()
    first_task = service._task
    assert first_task is not None
    assert service._loop is asyncio.get_running_loop()

    await service.start()
    assert service._task is first_task

    await service.stop()

    assert service._task is None
    assert service._loop is None


async def test_enqueue_p2p_message_uses_reaction_and_queue_text(tmp_path: Path) -> None:
    service, repo, feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )
    runtime = _build_runtime()

    first = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(
            event_id="evt-p2p-1",
            message_id="om_p2p_1",
            text="first",
            chat_id="oc_p2p_1",
            chat_type="p2p",
        ),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    second = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(
            event_id="evt-p2p-2",
            message_id="om_p2p_2",
            text="second",
            chat_id="oc_p2p_1",
            chat_type="p2p",
        ),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    assert first.status == "accepted"
    assert second.status == "accepted"
    _ = await service._retry_pending_reactions()
    _ = await service._retry_pending_queue_replies()
    assert feishu_client.reactions == [("om_p2p_1", "OK"), ("om_p2p_2", "OK")]
    assert feishu_client.reply_messages == [("om_p2p_2", _build_queue_reply_text(1))]
    assert feishu_client.sent_messages == []
    first_record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_p2p_1",
    )
    second_record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_p2p_2",
    )
    assert first_record.reaction_status == FeishuMessageDeliveryStatus.SENT
    assert first_record.ack_status == FeishuMessageDeliveryStatus.SKIPPED
    assert second_record.reaction_status == FeishuMessageDeliveryStatus.SENT
    assert second_record.ack_status == FeishuMessageDeliveryStatus.SENT


async def test_queue_reply_uses_chat_send_without_message_id(tmp_path: Path) -> None:
    service, repo, feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )
    runtime = _build_runtime()

    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="first"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-2", message_id="om_2", text="second"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    queued_record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_2",
    )
    _ = repo.update(queued_record.message_pool_id, message_id=None)

    assert await service._retry_pending_queue_replies() is True

    updated = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_2",
    )
    assert updated.ack_status == FeishuMessageDeliveryStatus.SENT
    assert feishu_client.sent_messages == [("oc_group_1", _build_queue_reply_text(1))]


async def test_process_and_finalize_p2p_message_run_uses_reply(tmp_path: Path) -> None:
    (
        service,
        repo,
        feishu_client,
        run_runtime_repo,
        event_log,
        _run_service,
    ) = _build_service(tmp_path)
    runtime = _build_runtime()
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(
            event_id="evt-p2p-1",
            message_id="om_p2p_1",
            text="hello",
            chat_id="oc_p2p_1",
            chat_type="p2p",
        ),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )

    assert await service._process_queued_messages() is True
    record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_p2p_1",
    )
    assert record.processing_status == FeishuMessageProcessingStatus.WAITING_RESULT
    assert record.run_id == "run-1"

    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
        last_error=None,
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"final answer"}',
        )
    )

    assert await service._finalize_waiting_results() is True
    updated = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_p2p_1",
    )
    assert updated.processing_status == FeishuMessageProcessingStatus.COMPLETED
    assert updated.final_reply_status == FeishuMessageDeliveryStatus.SENT
    assert feishu_client.reply_messages[-1] == ("om_p2p_1", "final answer")
    assert feishu_client.sent_messages == []


async def test_terminal_reply_uses_chat_send_without_message_id(tmp_path: Path) -> None:
    (
        service,
        repo,
        feishu_client,
        run_runtime_repo,
        event_log,
        _run_service,
    ) = _build_service(tmp_path)
    runtime = _build_runtime()
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="hello"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    _ = repo.update(record.message_pool_id, message_id=None)

    assert await service._process_queued_messages() is True
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
        last_error=None,
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"final answer"}',
        )
    )

    assert await service._finalize_waiting_results() is True
    updated = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert updated.final_reply_status == FeishuMessageDeliveryStatus.SENT
    assert feishu_client.sent_messages[-1] == ("oc_group_1", "final answer")


async def test_process_and_finalize_message_run(tmp_path: Path) -> None:
    (
        service,
        repo,
        feishu_client,
        run_runtime_repo,
        event_log,
        _run_service,
    ) = _build_service(tmp_path)
    runtime = _build_runtime()
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="hello"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )

    assert await service._process_queued_messages() is True
    record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert record.processing_status == FeishuMessageProcessingStatus.WAITING_RESULT
    assert record.run_id == "run-1"
    assert service.should_suppress_terminal_notification("run-1") is True

    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
        last_error=None,
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"final answer"}',
        )
    )

    assert await service._finalize_waiting_results() is True
    updated = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert updated.processing_status == FeishuMessageProcessingStatus.COMPLETED
    assert updated.final_reply_status == FeishuMessageDeliveryStatus.SENT
    assert feishu_client.reply_messages[-1] == ("om_1", "final answer")


async def test_enrich_sender_name_for_group_message(tmp_path: Path) -> None:
    service, _repo, feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )
    runtime = _build_runtime()
    feishu_client.user_names["ou_sender"] = "Sender Name"

    enriched = await service._enrich_sender_name(
        normalized=_build_message(
            event_id="evt-1",
            message_id="om_1",
            text="hello",
            sender_open_id="ou_sender",
        ),
        runtime_config=runtime,
    )

    assert enriched.sender_name == "Sender Name"
    assert enriched.metadata["sender_name"] == "Sender Name"


async def test_process_and_finalize_message_run_with_structured_output(
    tmp_path: Path,
) -> None:
    (
        service,
        repo,
        feishu_client,
        run_runtime_repo,
        event_log,
        _run_service,
    ) = _build_service(tmp_path)
    runtime = _build_runtime()
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="hello"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )

    assert await service._process_queued_messages() is True

    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = run_runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
        last_error=None,
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json=RunResult(
                trace_id="run-1",
                root_task_id="task-1",
                status="completed",
                output=content_parts_from_text("final answer"),
            ).model_dump_json(),
        )
    )

    assert await service._finalize_waiting_results() is True
    updated = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert updated.processing_status == FeishuMessageProcessingStatus.COMPLETED
    assert updated.final_reply_status == FeishuMessageDeliveryStatus.SENT
    assert feishu_client.reply_messages[-1] == ("om_1", "final answer")


async def test_finalize_waiting_result_sends_recovery_pause_notice_once(
    tmp_path: Path,
) -> None:
    (
        service,
        repo,
        feishu_client,
        run_runtime_repo,
        event_log,
        _run_service,
    ) = _build_service(tmp_path)
    runtime = _build_runtime()
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="hello"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )

    assert await service._process_queued_messages() is True

    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_RECOVERY,
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            event_type=RunEventType.RUN_PAUSED,
            payload_json='{"error_message":"stream interrupted"}',
        )
    )

    assert await service._finalize_waiting_results() is True
    record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert record.processing_status == FeishuMessageProcessingStatus.WAITING_RESULT
    assert feishu_client.reply_messages[-1] == (
        "om_1",
        _build_pause_reply(run_id="run-1", error_message="stream interrupted"),
    )
    assert await service._finalize_waiting_results() is False


async def test_stalled_waiting_result_is_requeued(tmp_path: Path) -> None:
    service, repo, _feishu_client, _run_runtime_repo, _event_log, run_service = (
        _build_service(tmp_path)
    )
    runtime = _build_runtime()
    run_service.fail_start_error = RuntimeError("no running event loop")

    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="hello"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    assert await service._process_queued_messages() is True
    record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert record.processing_status == FeishuMessageProcessingStatus.RETRYABLE_FAILED
    assert record.last_error == "no running event loop"


async def test_waiting_message_without_runtime_is_retried(tmp_path: Path) -> None:
    service, repo, _feishu_client, _run_runtime_repo, _event_log, _run_service = (
        _build_service(tmp_path)
    )
    runtime = _build_runtime()
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="hello"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    _ = await service._process_queued_messages()
    record = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    _ = repo.update(
        record.message_pool_id,
        updated_at=datetime.now(tz=timezone.utc) - timedelta(seconds=20),
    )

    assert await service._finalize_waiting_results() is True
    retried = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert retried.processing_status == FeishuMessageProcessingStatus.RETRYABLE_FAILED
    assert retried.last_error == "run_runtime_not_visible"


async def test_get_chat_summary_and_clear_chat(tmp_path: Path) -> None:
    (
        service,
        repo,
        _feishu_client,
        run_runtime_repo,
        _event_log,
        run_service,
    ) = _build_service(tmp_path)
    runtime = _build_runtime()

    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-1", message_id="om_1", text="first"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    _ = service.enqueue_message(
        runtime_config=runtime,
        normalized=_build_message(event_id="evt-2", message_id="om_2", text="second"),
        raw_body="{}",
        headers={},
        remote_addr=None,
    )
    _ = await service._process_queued_messages()
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
    )

    summary = service.get_chat_summary(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        chat_id="oc_group_1",
    )
    assert summary.active_total == 2
    assert summary.waiting_result_count == 1
    assert summary.queued_count == 1
    assert summary.processing_item is not None
    assert summary.processing_item.run_id == "run-1"
    assert summary.processing_item.blocking_reason == "awaiting_tool_approval"
    assert len(summary.queued_items) == 1

    clear_result = service.clear_chat(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        chat_id="oc_group_1",
    )
    assert clear_result.cleared_queue_count == 2
    assert clear_result.stopped_run_count == 1
    assert run_service.stopped == ["run-1"]
    cleared_records = repo.list_active_chat_messages(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        chat_id="oc_group_1",
    )
    assert cleared_records == ()
    first = repo.get_by_message_key(
        trigger_id="trg_feishu",
        tenant_key="tenant-1",
        message_key="om_1",
    )
    assert first.processing_status == FeishuMessageProcessingStatus.CANCELLED
    assert first.final_reply_status == FeishuMessageDeliveryStatus.SKIPPED
