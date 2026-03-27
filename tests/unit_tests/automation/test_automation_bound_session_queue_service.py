from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from agent_teams.automation import (
    AutomationBoundSessionQueueRepository,
    AutomationBoundSessionQueueService,
    AutomationDeliveryService,
    AutomationDeliveryEvent,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from agent_teams.gateway.feishu.models import FEISHU_PLATFORM, FeishuEnvironment
from agent_teams.media import content_parts_to_text
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.runs.run_models import IntentInput
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


class _FakeRunService:
    def __init__(self) -> None:
        self.created_intents: list[IntentInput] = []
        self.started_run_ids: list[str] = []

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        self.created_intents.append(intent)
        return (f"run-{len(self.created_intents)}", intent.session_id)

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)


class _FakeDeliveryService:
    def __init__(self) -> None:
        self.register_calls: list[dict[str, object]] = []

    def register_run(self, **kwargs: object) -> None:
        self.register_calls.append(kwargs)
        return None


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

    def send_text_message(self, *, chat_id: str, text: str, environment=None) -> None:
        _ = environment
        self.sent_messages.append({"chat_id": chat_id, "text": text})


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
    AutomationBoundSessionQueueRepository,
    RunRuntimeRepository,
    _FakeRunService,
    _FakeDeliveryService,
    _FakeFeishuClient,
    _FakeProjectRepository,
]:
    db_path = tmp_path / "automation-bound-session-queue.db"
    project = _build_project()
    binding_repo = ExternalSessionBindingRepository(db_path)
    binding_repo.upsert_binding(
        platform=FEISHU_PLATFORM,
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="oc_123",
        session_id="session-1",
    )
    queue_repo = AutomationBoundSessionQueueRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    run_service = _FakeRunService()
    delivery_service = _FakeDeliveryService()
    feishu_client = _FakeFeishuClient()
    project_repo = _FakeProjectRepository(project)
    service = AutomationBoundSessionQueueService(
        repository=queue_repo,
        external_session_binding_repo=binding_repo,
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
        delivery_service=cast(AutomationDeliveryService, delivery_service),
        runtime_config_lookup=_FakeRuntimeConfigLookup(),
        feishu_client=feishu_client,
        project_repository=project_repo,
    )
    return (
        service,
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
    assert queue_repo.count_non_terminal_by_session("session-1") == 0
    assert len(run_service.created_intents) == 1
    assert (
        content_parts_to_text(run_service.created_intents[0].input)
        == "Summarize the day."
    )
    assert (
        run_service.created_intents[0].conversation_context is not None
        and run_service.created_intents[0].conversation_context.im_force_direct_send
        is True
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
        queued_records[0].prompt == "定时任务触发：Daily Briefing\n\nSummarize the day."
    )
    assert (
        queued_records[0].queue_message
        == "定时任务 Daily Briefing 准备执行，当前任务前面有 1 个消息"
    )
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
        == "定时任务触发：Daily Briefing\n\nSummarize the day."
    )
    assert run_service.started_run_ids == ["run-1"]
    assert len(waiting_records) == 1
    assert waiting_records[0].run_id == "run-1"
    assert len(delivery_service.register_calls) == 1
    assert delivery_service.register_calls[0]["send_started"] is False
    assert project_repo.project.last_session_id == "session-1"
    assert project_repo.project.last_run_started_at is not None
