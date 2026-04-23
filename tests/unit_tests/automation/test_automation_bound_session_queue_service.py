from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from relay_teams.automation.automation_bound_session_queue_repository import (
    AutomationBoundSessionQueueRepository,
)
from relay_teams.automation.automation_bound_session_queue_service import (
    AutomationBoundSessionQueueService,
    AutomationBoundSessionQueueWorker,
)
from relay_teams.automation.automation_delivery_service import AutomationDeliveryService
from relay_teams.automation.automation_models import (
    AutomationBoundSessionQueueStatus,
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from relay_teams.gateway.feishu.models import FeishuEnvironment
from relay_teams.media import content_parts_to_text
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import ProjectKind, SessionRecord


class _FakeSessionLookup:
    def __init__(self, sessions: dict[str, SessionRecord]) -> None:
        self._sessions = sessions
        self.rebind_calls: list[tuple[str, str]] = []

    def get_session(self, session_id: str) -> SessionRecord:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return self._sessions[session_id]

    def rebind_session_workspace(
        self,
        session_id: str,
        *,
        workspace_id: str,
    ) -> SessionRecord:
        session = self.get_session(session_id)
        rebound = session.model_copy(update={"workspace_id": workspace_id})
        self._sessions[session_id] = rebound
        self.rebind_calls.append((session_id, workspace_id))
        return rebound


class _FakeRunService:
    def __init__(self) -> None:
        self.created_intents: list[IntentInput] = []
        self.started_run_ids: list[str] = []
        self.resume_run_ids: list[str] = []
        self.resume_errors: list[str] = []

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created_intents.append(intent)
        return (f"run-{len(self.created_intents)}", intent.session_id)

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)

    def resume_run(self, run_id: str) -> str:
        self.resume_run_ids.append(run_id)
        if self.resume_errors:
            raise RuntimeError(self.resume_errors.pop(0))
        return "session-1"


class _FakeDeliveryService:
    def __init__(self) -> None:
        self.register_calls: list[dict[str, object]] = []
        self.skipped_terminal_runs: list[tuple[str, str | None]] = []

    def register_run(self, **kwargs: object) -> None:
        self.register_calls.append(kwargs)
        return None

    def mark_terminal_delivery_skipped(
        self,
        *,
        run_id: str,
        terminal_message: str | None = None,
    ) -> None:
        self.skipped_terminal_runs.append((run_id, terminal_message))


class _FakeBoundSessionQueueWorkerService:
    def __init__(self) -> None:
        self.calls = 0

    def process_pending(self) -> bool:
        self.calls += 1
        return self.calls == 1


class _BlockingBoundSessionQueueWorkerService:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def process_pending(self) -> bool:
        self.entered.set()
        self.release.wait(timeout=2.0)
        self.finished.set()
        return False


class _FakeRuntimeConfigLookup:
    class _RuntimeConfig:
        def __init__(self) -> None:
            self.environment = FeishuEnvironment(
                app_id="app-1",
                app_secret="secret-1",
                verification_token="vt",
                encrypt_key="ek",
            )

    def get_runtime_config_by_trigger_id(self, trigger_id: str) -> _RuntimeConfig:
        _ = trigger_id
        return self._RuntimeConfig()


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, str]] = []
        self.reply_messages: list[dict[str, str]] = []
        self.deleted_messages: list[str] = []

    def send_text_message(self, *, chat_id: str, text: str, environment=None) -> str:
        _ = environment
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
        self.reply_messages.append({"message_id": message_id, "text": text})
        return f"om_reply_{len(self.reply_messages)}"

    def delete_message(self, *, message_id: str, environment=None) -> None:
        _ = environment
        self.deleted_messages.append(message_id)


class _FakeProjectRepository:
    def __init__(self, project: AutomationProjectRecord) -> None:
        self.project = project
        self.updated_projects: list[AutomationProjectRecord] = []

    def get(self, automation_project_id: str) -> AutomationProjectRecord:
        if automation_project_id != self.project.automation_project_id:
            raise KeyError(automation_project_id)
        return self.project

    def update(self, record: AutomationProjectRecord) -> AutomationProjectRecord:
        self.project = record
        self.updated_projects.append(record)
        return record


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
            trigger_id="trigger-1",
            tenant_key="tenant-1",
            chat_id="oc_123",
            session_id="session-1",
            chat_type="group",
            source_label="Release Updates",
        ),
        delivery_events=(
            AutomationDeliveryEvent.STARTED,
            AutomationDeliveryEvent.COMPLETED,
            AutomationDeliveryEvent.FAILED,
        ),
        trigger_id="schedule-aut_1",
    )


def _build_service(
    tmp_path: Path,
) -> tuple[
    AutomationBoundSessionQueueService,
    _FakeSessionLookup,
    AutomationBoundSessionQueueRepository,
    RunRuntimeRepository,
    _FakeRunService,
    _FakeDeliveryService,
    _FakeFeishuClient,
    _FakeProjectRepository,
]:
    db_path = tmp_path / "automation-bound-session-queue.db"
    project = _build_project()
    queue_repo = AutomationBoundSessionQueueRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    run_service = _FakeRunService()
    delivery_service = _FakeDeliveryService()
    feishu_client = _FakeFeishuClient()
    project_repo = _FakeProjectRepository(project)
    session_lookup = _FakeSessionLookup(
        {
            "session-1": SessionRecord(
                session_id="session-1",
                workspace_id="default",
                project_kind=ProjectKind.WORKSPACE,
                metadata={"title": "Bound Session"},
            )
        }
    )
    service = AutomationBoundSessionQueueService(
        repository=queue_repo,
        session_lookup=session_lookup,
        run_service=run_service,
        run_runtime_repo=run_runtime_repo,
        delivery_service=cast(AutomationDeliveryService, delivery_service),
        runtime_config_lookup=_FakeRuntimeConfigLookup(),
        feishu_client=feishu_client,
        project_repository=project_repo,
    )
    return (
        service,
        session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        delivery_service,
        feishu_client,
        project_repo,
    )


def _queue_and_start_bound_run(
    tmp_path: Path,
) -> tuple[
    AutomationBoundSessionQueueService,
    _FakeSessionLookup,
    AutomationBoundSessionQueueRepository,
    RunRuntimeRepository,
    _FakeRunService,
    _FakeDeliveryService,
    _FakeFeishuClient,
    _FakeProjectRepository,
]:
    (
        service,
        session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        delivery_service,
        feishu_client,
        project_repo,
    ) = _build_service(tmp_path)
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
        )
    )
    _ = service.materialize_execution(project=_build_project(), reason="schedule")
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    _ = service.process_pending()
    return (
        service,
        session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        delivery_service,
        feishu_client,
        project_repo,
    )


def test_materialize_execution_starts_in_bound_session_when_idle(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        _run_runtime_repo,
        run_service,
        delivery_service,
        feishu_client,
        _project_repo,
    ) = _build_service(tmp_path)

    handle = service.materialize_execution(project=_build_project(), reason="schedule")

    assert handle is not None
    assert handle.session_id == "session-1"
    assert handle.run_id == "run-1"
    assert handle.queued is False
    waiting_records = queue_repo.list_waiting_for_result(limit=10)
    assert len(waiting_records) == 1
    assert waiting_records[0].run_id == "run-1"
    assert len(run_service.created_intents) == 1
    assert (
        content_parts_to_text(run_service.created_intents[0].input)
        == "触发定时任务 “Daily Briefing”：\nSummarize the day."
    )
    assert (
        run_service.created_intents[0].conversation_context is not None
        and run_service.created_intents[0].conversation_context.im_reply_to_message_id
        is None
    )
    assert run_service.started_run_ids == ["run-1"]
    assert len(delivery_service.register_calls) == 1
    assert delivery_service.register_calls[0]["send_started"] is True
    assert feishu_client.sent_messages == []


def test_materialize_execution_queues_when_bound_session_is_busy(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        _delivery_service,
        feishu_client,
        _project_repo,
    ) = _build_service(tmp_path)
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
        )
    )

    handle = service.materialize_execution(project=_build_project(), reason="schedule")
    queued_records = queue_repo.list_ready_to_start(
        ready_at=datetime.now(tz=timezone.utc),
        limit=10,
    )

    assert handle is not None
    assert handle.session_id == "session-1"
    assert handle.run_id is None
    assert handle.queued is True
    assert len(run_service.created_intents) == 0
    assert len(queued_records) == 1
    assert (
        queued_records[0].prompt
        == "触发定时任务 “Daily Briefing”：\nSummarize the day."
    )
    assert (
        queued_records[0].queue_message
        == "定时任务 Daily Briefing 准备执行，当前任务前面有 1 个消息"
    )
    assert queued_records[0].queue_message_id == "om_1"
    assert queued_records[0].queue_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert feishu_client.sent_messages == [
        {
            "chat_id": "oc_123",
            "text": "定时任务 Daily Briefing 准备执行，当前任务前面有 1 个消息",
        }
    ]


def test_process_pending_starts_queued_run_after_bound_session_becomes_idle(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        delivery_service,
        _feishu_client,
        project_repo,
    ) = _build_service(tmp_path)
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
        )
    )
    _ = service.materialize_execution(project=_build_project(), reason="manual")
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )

    progressed = service.process_pending()
    waiting_records = queue_repo.list_waiting_for_result(limit=10)

    assert progressed is True
    assert len(run_service.created_intents) == 1
    assert (
        content_parts_to_text(run_service.created_intents[0].input)
        == "触发定时任务 “Daily Briefing”：\nSummarize the day."
    )
    assert run_service.started_run_ids == ["run-1"]
    assert len(waiting_records) == 1
    assert waiting_records[0].run_id == "run-1"
    assert waiting_records[0].queue_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert len(delivery_service.register_calls) == 1
    assert delivery_service.register_calls[0]["send_started"] is False
    assert project_repo.project.last_session_id == "session-1"
    assert project_repo.project.last_run_started_at is not None
    assert delivery_service.register_calls[0]["reply_to_message_id"] == "om_1"
    assert run_service.created_intents[0].conversation_context is not None
    assert (
        run_service.created_intents[0].conversation_context.im_reply_to_message_id
        == "om_1"
    )
    assert _feishu_client.deleted_messages == []


def test_materialize_execution_fails_when_bound_session_is_missing(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        _queue_repo,
        _run_runtime_repo,
        run_service,
        _delivery_service,
        _feishu_client,
        _project_repo,
    ) = _build_service(tmp_path)
    project = _build_project().model_copy(
        update={
            "delivery_binding": AutomationFeishuBinding(
                trigger_id="trigger-1",
                tenant_key="tenant-1",
                chat_id="oc_123",
                session_id="missing-session",
                chat_type="group",
                source_label="Release Updates",
            )
        }
    )

    try:
        _ = service.materialize_execution(project=project, reason="schedule")
    except RuntimeError as exc:
        assert "missing_bound_session:missing-session" in str(exc)
    else:
        raise AssertionError("Expected missing bound session to fail")
    assert run_service.created_intents == []


def test_process_pending_schedules_recoverable_resume_with_backoff(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        _delivery_service,
        _feishu_client,
        _project_repo,
    ) = _queue_and_start_bound_run(tmp_path)
    waiting = queue_repo.list_waiting_for_result(limit=10)[0]
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id=str(waiting.run_id),
            session_id="session-1",
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )

    progressed = service.process_pending()

    updated = queue_repo.list_waiting_for_result(limit=10)[0]
    assert progressed is True
    assert run_service.resume_run_ids == []
    assert updated.resume_attempts == 0
    assert updated.last_error == "stream interrupted"
    assert updated.resume_next_attempt_at > updated.updated_at


def test_process_pending_requests_resume_after_backoff_elapsed(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        _delivery_service,
        _feishu_client,
        _project_repo,
    ) = _queue_and_start_bound_run(tmp_path)
    waiting = queue_repo.list_waiting_for_result(limit=10)[0]
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id=str(waiting.run_id),
            session_id="session-1",
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )
    _ = queue_repo.update(
        waiting.model_copy(
            update={
                "updated_at": waiting.updated_at - timedelta(seconds=1),
                "resume_next_attempt_at": waiting.updated_at,
            }
        )
    )

    progressed = service.process_pending()

    updated = queue_repo.list_waiting_for_result(limit=10)[0]
    assert progressed is True
    assert run_service.resume_run_ids == ["run-1"]
    assert updated.resume_attempts == 1
    assert updated.last_error is None


def test_materialize_execution_treats_dirty_failed_recovery_run_as_busy(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        _delivery_service,
        feishu_client,
        _project_repo,
    ) = _build_service(tmp_path)
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )

    handle = service.materialize_execution(project=_build_project(), reason="schedule")

    assert handle is not None
    assert handle.queued is True
    assert run_service.created_intents == []
    queued_records = queue_repo.list_ready_to_start(
        ready_at=datetime.now(tz=timezone.utc),
        limit=10,
    )
    assert len(queued_records) == 1
    assert feishu_client.sent_messages[0]["text"].startswith(
        "定时任务 Daily Briefing 准备执行"
    )
    repaired = run_runtime_repo.get("active-run-1")
    assert repaired is not None
    assert repaired.status == RunRuntimeStatus.PAUSED


def test_process_pending_exhausts_resume_attempts_and_skips_terminal_delivery(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        delivery_service,
        feishu_client,
        _project_repo,
    ) = _queue_and_start_bound_run(tmp_path)
    waiting = queue_repo.list_waiting_for_result(limit=10)[0]
    run_service.resume_errors = ["still paused"]
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id=str(waiting.run_id),
            session_id="session-1",
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )
    _ = queue_repo.update(
        waiting.model_copy(
            update={
                "resume_attempts": 4,
                "updated_at": waiting.updated_at - timedelta(seconds=1),
                "resume_next_attempt_at": waiting.updated_at,
            }
        )
    )

    progressed = service.process_pending()

    failed_record = queue_repo.get(waiting.automation_queue_id)
    assert failed_record is not None
    assert progressed is True
    assert run_service.resume_run_ids == ["run-1"]
    assert failed_record.status == AutomationBoundSessionQueueStatus.FAILED
    assert failed_record.queue_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert feishu_client.reply_messages
    assert "自动恢复失败" in feishu_client.reply_messages[-1]["text"]
    assert feishu_client.deleted_messages == []
    assert delivery_service.skipped_terminal_runs == [
        ("run-1", feishu_client.reply_messages[-1]["text"])
    ]


def test_materialize_execution_rebinds_bound_session_workspace_before_start(
    tmp_path: Path,
) -> None:
    (
        service,
        session_lookup,
        _queue_repo,
        _run_runtime_repo,
        run_service,
        _delivery_service,
        _feishu_client,
        _project_repo,
    ) = _build_service(tmp_path)
    session_lookup._sessions["session-1"] = session_lookup._sessions[
        "session-1"
    ].model_copy(update={"workspace_id": "stale-worktree"})
    project = _build_project().model_copy(update={"workspace_id": "fresh-worktree"})

    handle = service.materialize_execution(project=project, reason="manual")

    assert handle is not None
    assert session_lookup.rebind_calls == [("session-1", "fresh-worktree")]
    assert run_service.created_intents[0].reuse_root_instance is True


def test_direct_start_waiting_record_auto_resumes_recoverable_runtime(
    tmp_path: Path,
) -> None:
    (
        service,
        _session_lookup,
        queue_repo,
        run_runtime_repo,
        run_service,
        _delivery_service,
        _feishu_client,
        _project_repo,
    ) = _build_service(tmp_path)
    _ = service.materialize_execution(project=_build_project(), reason="schedule")
    waiting = queue_repo.list_waiting_for_result(limit=10)[0]
    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id=str(waiting.run_id),
            session_id="session-1",
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )
    _ = queue_repo.update(
        waiting.model_copy(
            update={
                "updated_at": waiting.updated_at - timedelta(seconds=1),
                "resume_next_attempt_at": waiting.updated_at,
            }
        )
    )

    progressed = service.process_pending()

    updated = queue_repo.list_waiting_for_result(limit=10)[0]
    assert progressed is True
    assert run_service.resume_run_ids == ["run-1"]
    assert updated.resume_attempts == 1
    assert updated.last_error is None


def test_bound_session_queue_worker_start_wake_stop() -> None:
    async def run_worker() -> None:
        service = _FakeBoundSessionQueueWorkerService()
        worker = AutomationBoundSessionQueueWorker(
            queue_service=cast(AutomationBoundSessionQueueService, service),
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


def test_bound_session_queue_worker_stop_waits_for_inflight_processing() -> None:
    async def run_worker() -> None:
        service = _BlockingBoundSessionQueueWorkerService()
        worker = AutomationBoundSessionQueueWorker(
            queue_service=cast(AutomationBoundSessionQueueService, service),
            poll_interval_seconds=0.01,
        )

        await worker.start()
        assert await asyncio.to_thread(service.entered.wait, 1.0)

        stop_task = asyncio.create_task(worker.stop())
        await asyncio.sleep(0.03)
        assert not stop_task.done()

        service.release.set()
        await asyncio.wait_for(stop_task, timeout=1.0)
        assert service.finished.is_set()

    asyncio.run(run_worker())
