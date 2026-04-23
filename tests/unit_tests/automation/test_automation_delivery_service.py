from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import cast

from relay_teams.automation.automation_delivery_repository import (
    AutomationDeliveryRepository,
)
from relay_teams.automation.automation_delivery_service import (
    AutomationDeliveryService,
    AutomationDeliveryWorker,
)
from relay_teams.automation.automation_models import (
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationDeliveryStatus,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from relay_teams.gateway.feishu.models import FeishuEnvironment
from relay_teams.media import content_parts_from_text
from relay_teams.notifications import NotificationContext, NotificationType
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import RunEvent, RunResult
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)


class _FakeRuntimeConfigLookup:
    class _RuntimeConfig:
        def __init__(self, trigger_id: str) -> None:
            self.environment = FeishuEnvironment(
                app_id=f"cli_{trigger_id}",
                app_secret="secret",
                app_name="Agent Teams Bot",
            )

    def get_runtime_config_by_trigger_id(self, trigger_id: str) -> _RuntimeConfig:
        return self._RuntimeConfig(trigger_id)


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, str]] = []
        self.reply_messages: list[dict[str, str]] = []
        self.deleted_messages: list[str] = []
        self.fail_delete = False
        self.fail_send_error: RuntimeError | None = None
        self.fail_reply_error: RuntimeError | None = None

    def send_text_message(self, *, chat_id: str, text: str, environment=None) -> str:
        _ = environment
        if self.fail_send_error is not None:
            raise self.fail_send_error
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return f"om_{len(self.sent_messages)}"

    def reply_text_message(
        self,
        *,
        message_id: str,
        text: str,
        environment=None,
    ) -> str:
        _ = environment
        if self.fail_reply_error is not None:
            raise self.fail_reply_error
        self.reply_messages.append({"message_id": message_id, "text": text})
        return f"om_reply_{len(self.reply_messages)}"

    def delete_message(self, *, message_id: str, environment=None) -> None:
        _ = environment
        if self.fail_delete:
            raise RuntimeError("delete_failed")
        self.deleted_messages.append(message_id)


class _FakeAutomationDeliveryWorkerService:
    def __init__(self) -> None:
        self.calls = 0

    def process_pending(self) -> bool:
        self.calls += 1
        return self.calls == 1


class _BlockingAutomationDeliveryWorkerService:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def process_pending(self) -> bool:
        self.entered.set()
        _ = self.release.wait(timeout=2.0)
        self.finished.set()
        return False


class _FakeNotificationService:
    def __init__(self) -> None:
        self.emit_calls: list[dict[str, object]] = []

    def emit(
        self,
        *,
        notification_type: NotificationType,
        title: str,
        body: str,
        context: NotificationContext,
        dedupe_key: str | None = None,
    ) -> bool:
        self.emit_calls.append(
            {
                "notification_type": notification_type,
                "title": title,
                "body": body,
                "context": context,
                "dedupe_key": dedupe_key,
            }
        )
        return True


def _build_project() -> AutomationProjectRecord:
    return AutomationProjectRecord(
        automation_project_id="aut_1",
        name="daily-briefing",
        display_name="Daily Briefing",
        status=AutomationProjectStatus.ENABLED,
        workspace_id="default",
        prompt="Summarize the day.",
        schedule_mode=AutomationScheduleMode.CRON,
        cron_expression="0 9 * * *",
        timezone="UTC",
        run_config=AutomationRunConfig(),
        delivery_binding=AutomationFeishuBinding(
            trigger_id="trg_feishu",
            tenant_key="tenant-1",
            chat_id="oc_123",
            chat_type="group",
            source_label="Release Updates",
        ),
        delivery_events=(
            AutomationDeliveryEvent.STARTED,
            AutomationDeliveryEvent.COMPLETED,
            AutomationDeliveryEvent.FAILED,
        ),
        trigger_id="trg_schedule",
    )


def _build_service(
    tmp_path: Path,
) -> tuple[
    AutomationDeliveryService,
    _FakeFeishuClient,
    RunRuntimeRepository,
    EventLog,
    AutomationDeliveryRepository,
    _FakeNotificationService,
]:
    db_path = tmp_path / "automation-delivery.db"
    repository = AutomationDeliveryRepository(db_path)
    feishu_client = _FakeFeishuClient()
    notification_service = _FakeNotificationService()
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    service = AutomationDeliveryService(
        repository=repository,
        runtime_config_lookup=_FakeRuntimeConfigLookup(),
        feishu_client=feishu_client,
        run_runtime_repo=run_runtime_repo,
        event_log=event_log,
        notification_service=notification_service,
    )
    return (
        service,
        feishu_client,
        run_runtime_repo,
        event_log,
        repository,
        notification_service,
    )


def test_register_run_sends_started_message_immediately(tmp_path: Path) -> None:
    service, feishu_client, _run_runtime_repo, _event_log, repository, _ = (
        _build_service(tmp_path)
    )

    record = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="manual",
    )

    assert record is not None
    assert len(feishu_client.sent_messages) == 1
    assert feishu_client.sent_messages[0]["text"] == "定时任务 Daily Briefing 开始执行"
    persisted = repository.get_by_run_id("run-1")
    assert persisted.started_status.value == "sent"
    assert persisted.started_message_id == "om_1"
    assert persisted.terminal_status.value == "pending"


def test_attempt_started_delivery_claim_prevents_duplicate_send(tmp_path: Path) -> None:
    service, feishu_client, _run_runtime_repo, _event_log, repository, _ = (
        _build_service(tmp_path)
    )
    persisted = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="manual",
    )

    assert persisted is not None
    stale_record = repository.get_by_run_id("run-1").model_copy(
        update={"started_status": AutomationDeliveryStatus.PENDING}
    )

    progressed = service._attempt_started_delivery(stale_record)

    assert progressed is False
    assert len(feishu_client.sent_messages) == 1


def test_process_pending_sends_completed_message_when_run_finishes(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository, _ = _build_service(
        tmp_path
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"Daily report is ready."}',
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    progressed = service.process_pending()

    assert progressed is True
    assert len(feishu_client.sent_messages) == 1
    assert feishu_client.reply_messages == [
        {"message_id": "om_1", "text": "Daily report is ready."}
    ]
    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status.value == "sent"
    assert persisted.terminal_event == AutomationDeliveryEvent.COMPLETED
    assert persisted.terminal_message_id == "om_reply_1"
    assert persisted.started_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert feishu_client.deleted_messages == []


def test_process_pending_skips_completed_message_when_run_has_no_output(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository, _ = _build_service(
        tmp_path
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"   "}',
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    progressed = service.process_pending()

    assert progressed is True
    assert len(feishu_client.sent_messages) == 1
    assert feishu_client.reply_messages == []
    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status.value == "skipped"
    assert persisted.terminal_event == AutomationDeliveryEvent.COMPLETED
    assert persisted.terminal_message == ""
    assert persisted.started_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert feishu_client.deleted_messages == []


def test_process_pending_sends_structured_completed_message_when_run_finishes(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository, _ = _build_service(
        tmp_path
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json=RunResult(
                trace_id="run-1",
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text("Daily report is ready."),
            ).model_dump_json(),
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    progressed = service.process_pending()

    assert progressed is True
    assert len(feishu_client.sent_messages) == 1
    assert feishu_client.reply_messages == [
        {"message_id": "om_1", "text": "Daily report is ready."}
    ]
    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status.value == "sent"
    assert persisted.terminal_event == AutomationDeliveryEvent.COMPLETED
    assert persisted.started_cleanup_status == AutomationCleanupStatus.SKIPPED


def test_process_pending_uses_terminal_error_when_failed_output_is_empty(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository, _ = _build_service(
        tmp_path
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_FAILED,
            payload_json='{"status":"failed","output":"","error":"provider timeout"}',
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    progressed = service.process_pending()

    assert progressed is True
    assert len(feishu_client.sent_messages) == 1
    assert len(feishu_client.reply_messages) == 1
    assert "provider timeout" in feishu_client.reply_messages[0]["text"]
    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status.value == "sent"
    assert persisted.terminal_event == AutomationDeliveryEvent.FAILED
    assert persisted.started_cleanup_status == AutomationCleanupStatus.SKIPPED


def test_process_pending_defers_failed_delivery_while_run_is_awaiting_recovery(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, _event_log, repository, _ = (
        _build_service(tmp_path)
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )

    progressed = service.process_pending()

    persisted = repository.get_by_run_id("run-1")
    assert progressed is False
    assert len(feishu_client.sent_messages) == 1
    assert persisted.terminal_status == AutomationDeliveryStatus.PENDING
    assert persisted.terminal_message_id is None


def test_process_pending_cleanup_failure_does_not_break_terminal_delivery(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository, _ = _build_service(
        tmp_path
    )
    feishu_client.fail_delete = True
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"Daily report is ready."}',
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    progressed = service.process_pending()

    persisted = repository.get_by_run_id("run-1")
    assert progressed is True
    assert len(feishu_client.sent_messages) == 1
    assert feishu_client.reply_messages == [
        {"message_id": "om_1", "text": "Daily report is ready."}
    ]
    assert feishu_client.deleted_messages == []
    assert persisted.terminal_status == AutomationDeliveryStatus.SENT
    assert persisted.started_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert persisted.started_cleanup_attempts == 0


def test_delivery_service_suppresses_generic_terminal_notification_for_owned_run(
    tmp_path: Path,
) -> None:
    service, _feishu_client, _run_runtime_repo, _event_log, _repository, _ = (
        _build_service(tmp_path)
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )

    assert service.should_suppress_terminal_notification("run-1") is True
    assert service.should_suppress_terminal_notification("missing-run") is False


def test_delivery_service_does_not_suppress_when_terminal_delivery_is_disabled(
    tmp_path: Path,
) -> None:
    service, _feishu_client, _run_runtime_repo, _event_log, _repository, _ = (
        _build_service(tmp_path)
    )
    project = _build_project().model_copy(update={"delivery_events": ()})
    _ = service.register_run(
        project=project,
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )

    assert service.should_suppress_terminal_notification("run-1") is False


def test_delivery_service_emits_fallback_notification_after_terminal_delivery_failure(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository, notifications = (
        _build_service(tmp_path)
    )
    _ = service.register_run(
        project=_build_project(),
        session_id="session-1",
        run_id="run-1",
        reason="schedule",
    )
    feishu_client.fail_reply_error = RuntimeError("reply_failed")
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.TERMINAL,
            last_error="provider timeout",
        )
    )
    event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_FAILED,
            payload_json='{"status":"failed","output":"","error":"provider timeout"}',
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    for _ in range(5):
        assert service.process_pending() is True

    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status == AutomationDeliveryStatus.FAILED
    assert notifications.emit_calls == [
        {
            "notification_type": NotificationType.RUN_FAILED,
            "title": "Run Failed",
            "body": "定时任务 Daily Briefing 执行失败。\n\nprovider timeout",
            "context": NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
            ),
            "dedupe_key": "automation-terminal-fallback:run-1",
        }
    ]
    assert service.should_suppress_terminal_notification("run-1") is False


def test_automation_delivery_worker_start_wake_stop() -> None:
    async def run_worker() -> None:
        service = _FakeAutomationDeliveryWorkerService()
        worker = AutomationDeliveryWorker(
            delivery_service=cast(AutomationDeliveryService, service),
            poll_interval_seconds=0.01,
        )

        await worker.stop()
        await worker.start()
        await worker.start()
        worker.wake()
        await asyncio.sleep(0.03)
        await worker.stop()

        assert service.calls >= 2

    asyncio.run(run_worker())


def test_automation_delivery_worker_stop_waits_for_inflight_processing() -> None:
    async def run_worker() -> None:
        service = _BlockingAutomationDeliveryWorkerService()
        worker = AutomationDeliveryWorker(
            delivery_service=cast(AutomationDeliveryService, service),
            poll_interval_seconds=0.01,
        )

        await worker.start()
        assert await asyncio.to_thread(service.entered.wait, 1.0)
        stop_task = asyncio.create_task(worker.stop())
        await asyncio.sleep(0.03)
        assert stop_task.done() is False

        service.release.set()
        await asyncio.wait_for(stop_task, timeout=1.0)

        assert service.finished.is_set()

    asyncio.run(run_worker())
