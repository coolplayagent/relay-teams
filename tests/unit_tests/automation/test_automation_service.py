from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.automation import (
    AutomationProjectCreateInput,
    AutomationProjectRepository,
    AutomationProjectStatus,
    AutomationScheduleMode,
    AutomationService,
)
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.session_service import SessionService
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.triggers.trigger_repository import TriggerRepository
from agent_teams.triggers.trigger_models import TriggerStatus
from agent_teams.triggers.trigger_service import TriggerService


class _FakeRunManager:
    def __init__(self) -> None:
        self.create_calls: list[object] = []
        self.started_run_ids: list[str] = []

    def create_run(self, intent) -> tuple[str, str]:
        self.create_calls.append(intent)
        return (f"run-{len(self.create_calls)}", intent.session_id)

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)


def _build_session_service(db_path: Path) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
    )


def _build_service(tmp_path: Path) -> tuple[AutomationService, _FakeRunManager]:
    db_path = tmp_path / "automation.db"
    run_manager = _FakeRunManager()
    service = AutomationService(
        repository=AutomationProjectRepository(db_path),
        trigger_service=TriggerService(trigger_repo=TriggerRepository(db_path)),
        session_service=_build_session_service(db_path),
        run_service=cast(RunManager, run_manager),
    )
    return service, run_manager


def test_create_project_sets_next_run_at_for_cron(tmp_path: Path) -> None:
    service, _ = _build_service(tmp_path)

    created = service.create_project(
        AutomationProjectCreateInput(
            name="daily-briefing",
            workspace_id="default",
            prompt="Summarize the day.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * 1-5",
            timezone="UTC",
        )
    )

    assert created.trigger_id.startswith("trg_")
    assert created.next_run_at is not None
    assert created.status.value == "enabled"


def test_run_now_creates_automation_session_and_starts_run(tmp_path: Path) -> None:
    service, run_manager = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="nightly-report",
            workspace_id="default",
            prompt="Draft a nightly report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
        )
    )

    result = service.run_now(created.automation_project_id)
    sessions = service.list_project_sessions(created.automation_project_id)

    assert result["automation_project_id"] == created.automation_project_id
    assert len(sessions) == 1
    session_payload = cast(dict[str, object], sessions[0])
    metadata = cast(dict[str, str], session_payload["metadata"])
    assert session_payload["workspace_id"] == "default"
    assert session_payload["project_kind"] == "automation"
    assert session_payload["project_id"] == created.automation_project_id
    assert metadata["automation_reason"] == "manual"
    assert len(run_manager.create_calls) == 1
    assert run_manager.started_run_ids == ["run-1"]


def test_process_due_projects_runs_one_shot_once_and_disables_it(
    tmp_path: Path,
) -> None:
    service, run_manager = _build_service(tmp_path)
    run_at = datetime.now(tz=UTC) + timedelta(minutes=5)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="one-shot-report",
            workspace_id="default",
            prompt="Run once.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            run_at=run_at,
            timezone="UTC",
        )
    )

    processed = service.process_due_projects(now=run_at + timedelta(minutes=1))
    updated = service.get_project(created.automation_project_id)
    sessions = service.list_project_sessions(created.automation_project_id)

    assert processed == (created.automation_project_id,)
    assert updated.status.value == "disabled"
    assert updated.next_run_at is None
    assert len(sessions) == 1
    session_payload = cast(dict[str, object], sessions[0])
    metadata = cast(dict[str, str], session_payload["metadata"])
    assert metadata["automation_reason"] == "schedule"
    assert run_manager.started_run_ids == ["run-1"]


def test_enable_project_reenables_backing_trigger_for_manual_run(
    tmp_path: Path,
) -> None:
    service, run_manager = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="disabled-report",
            workspace_id="default",
            prompt="Run on demand.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            enabled=False,
        )
    )

    enabled = service.set_project_status(
        created.automation_project_id,
        status=AutomationProjectStatus.ENABLED,
    )
    trigger = service._trigger_service.get_trigger(created.trigger_id)
    result = service.run_now(created.automation_project_id)

    assert enabled.status.value == "enabled"
    assert trigger.status == TriggerStatus.ENABLED
    assert result["automation_project_id"] == created.automation_project_id
    assert run_manager.started_run_ids == ["run-1"]
