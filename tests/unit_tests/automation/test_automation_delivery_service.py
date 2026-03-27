from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_teams.automation import (
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

    def send_text_message(self, *, chat_id: str, text: str, environment=None) -> None:
        _ = environment
        self.sent_messages.append({"chat_id": chat_id, "text": text})


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
]:
    db_path = tmp_path / "automation-delivery.db"
    repository = AutomationDeliveryRepository(db_path)
    feishu_client = _FakeFeishuClient()
    run_runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    service = AutomationDeliveryService(
        repository=repository,
        runtime_config_lookup=_FakeRuntimeConfigLookup(),
        feishu_client=feishu_client,
        run_runtime_repo=run_runtime_repo,
        event_log=event_log,
    )
    return service, feishu_client, run_runtime_repo, event_log, repository


def test_register_run_sends_started_message_immediately(tmp_path: Path) -> None:
    service, feishu_client, _run_runtime_repo, _event_log, repository = _build_service(
        tmp_path
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
    assert persisted.terminal_status.value == "pending"


def test_attempt_started_delivery_claim_prevents_duplicate_send(tmp_path: Path) -> None:
    service, feishu_client, _run_runtime_repo, _event_log, repository = _build_service(
        tmp_path
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
    service, feishu_client, run_runtime_repo, event_log, repository = _build_service(
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
    assert len(feishu_client.sent_messages) == 2
    assert feishu_client.sent_messages[1]["text"] == "Daily report is ready."
    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status.value == "sent"
    assert persisted.terminal_event == AutomationDeliveryEvent.COMPLETED


def test_process_pending_skips_completed_message_when_run_has_no_output(
    tmp_path: Path,
) -> None:
    service, feishu_client, run_runtime_repo, event_log, repository = _build_service(
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
    persisted = repository.get_by_run_id("run-1")
    assert persisted.terminal_status.value == "skipped"
    assert persisted.terminal_event == AutomationDeliveryEvent.COMPLETED
    assert persisted.terminal_message == ""
