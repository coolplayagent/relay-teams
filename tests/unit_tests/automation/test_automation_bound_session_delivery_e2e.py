from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_teams.automation import (
    AutomationBoundSessionQueueRepository,
    AutomationBoundSessionQueueService,
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationDeliveryRepository,
    AutomationDeliveryService,
    AutomationDeliveryStatus,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from agent_teams.gateway.feishu.models import FeishuEnvironment
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.session_models import ProjectKind, SessionRecord


class _FakeSessionLookup:
    def __init__(self, sessions: dict[str, SessionRecord]) -> None:
        self._sessions = sessions

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
        return rebound


class _FakeRunService:
    def __init__(self) -> None:
        self.started_run_ids: list[str] = []
        self.resume_run_ids: list[str] = []

    def create_detached_run(self, intent: object) -> tuple[str, str]:
        _ = intent
        return ("run-1", "session-1")

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)

    def resume_run(self, run_id: str) -> str:
        self.resume_run_ids.append(run_id)
        return "session-1"


class _FakeRuntimeConfigLookup:
    class _RuntimeConfig:
        def __init__(self) -> None:
            self.environment = FeishuEnvironment(
                app_id="cli_demo",
                app_secret="secret",
                app_name="Agent Teams Bot",
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

    def get(self, automation_project_id: str) -> AutomationProjectRecord:
        if automation_project_id != self.project.automation_project_id:
            raise KeyError(automation_project_id)
        return self.project

    def update(self, record: AutomationProjectRecord) -> AutomationProjectRecord:
        self.project = record
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
            chat_type="p2p",
            source_label="Daily Briefing",
        ),
        delivery_events=(
            AutomationDeliveryEvent.STARTED,
            AutomationDeliveryEvent.COMPLETED,
            AutomationDeliveryEvent.FAILED,
        ),
        trigger_id="schedule-aut_1",
    )


def test_bound_queue_and_delivery_services_resume_without_premature_failed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "automation-bound-session-delivery-e2e.db"
    project = _build_project()
    queue_repo = AutomationBoundSessionQueueRepository(db_path)
    delivery_repo = AutomationDeliveryRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    run_service = _FakeRunService()
    feishu_client = _FakeFeishuClient()
    runtime_config_lookup = _FakeRuntimeConfigLookup()
    delivery_service = AutomationDeliveryService(
        repository=delivery_repo,
        runtime_config_lookup=runtime_config_lookup,
        feishu_client=feishu_client,
        run_runtime_repo=run_runtime_repo,
        event_log=event_log,
    )
    queue_service = AutomationBoundSessionQueueService(
        repository=queue_repo,
        session_lookup=_FakeSessionLookup(
            {
                "session-1": SessionRecord(
                    session_id="session-1",
                    workspace_id="default",
                    project_kind=ProjectKind.WORKSPACE,
                    metadata={"title": "Bound Session"},
                )
            }
        ),
        run_service=run_service,
        run_runtime_repo=run_runtime_repo,
        delivery_service=delivery_service,
        runtime_config_lookup=runtime_config_lookup,
        feishu_client=feishu_client,
        project_repository=_FakeProjectRepository(project),
    )

    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
        )
    )

    handle = queue_service.materialize_execution(project=project, reason="schedule")

    assert handle is not None
    assert handle.queued is True
    assert feishu_client.sent_messages == [
        {
            "chat_id": "oc_123",
            "text": "定时任务 Daily Briefing 准备执行，当前任务前面有 1 个消息",
        }
    ]

    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="active-run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )

    assert queue_service.process_pending() is True
    waiting_record = queue_repo.list_waiting_for_result(limit=10)[0]
    delivery_record = delivery_repo.get_by_run_id("run-1")
    assert waiting_record.run_id == "run-1"
    assert waiting_record.queue_cleanup_status == AutomationCleanupStatus.SKIPPED
    assert delivery_record.started_status == AutomationDeliveryStatus.SKIPPED
    assert feishu_client.deleted_messages == []

    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.AWAITING_RECOVERY,
            last_error="stream interrupted",
        )
    )

    assert delivery_service.process_pending() is False
    assert [message["text"] for message in feishu_client.sent_messages] == [
        "定时任务 Daily Briefing 准备执行，当前任务前面有 1 个消息"
    ]

    assert queue_service.process_pending() is True
    waiting_record = queue_repo.list_waiting_for_result(limit=10)[0]
    assert waiting_record.resume_attempts == 0
    assert waiting_record.resume_next_attempt_at > waiting_record.updated_at

    _ = queue_repo.update(
        waiting_record.model_copy(
            update={
                "resume_next_attempt_at": datetime.now(tz=timezone.utc),
            }
        )
    )

    assert queue_service.process_pending() is True
    assert run_service.resume_run_ids == ["run-1"]

    _ = run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        )
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed","output":"Recovered report is ready."}',
            occurred_at=datetime.now(tz=timezone.utc),
        )
    )

    assert delivery_service.process_pending() is True
    delivery_record = delivery_repo.get_by_run_id("run-1")
    assert delivery_record.terminal_status == AutomationDeliveryStatus.SENT
    assert delivery_record.terminal_event == AutomationDeliveryEvent.COMPLETED
    assert delivery_record.terminal_message == "Recovered report is ready."
    assert delivery_record.terminal_message_id == "om_reply_1"
    assert all(
        "执行失败" not in message["text"] for message in feishu_client.sent_messages
    )
    assert feishu_client.reply_messages == [
        {"message_id": "om_1", "text": "Recovered report is ready."}
    ]
