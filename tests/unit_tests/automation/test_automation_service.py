from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import cast

import pytest

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.automation import (
    AutomationBoundSessionQueueService,
    AutomationExecutionHandle,
    AutomationEventRepository,
    AutomationFeishuBinding,
    AutomationFeishuBindingService,
    AutomationProjectCreateInput,
    AutomationProjectRepository,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationScheduleMode,
    AutomationService,
)
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.workspace import WorkspaceRepository, WorkspaceService


class _FakeRunManager:
    def __init__(self) -> None:
        self.create_calls: list[object] = []
        self.started_run_ids: list[str] = []

    def create_run(self, intent: object) -> tuple[str, str]:
        session_id = getattr(intent, "session_id")
        self.create_calls.append(intent)
        return (f"run-{len(self.create_calls)}", cast(str, session_id))

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)


class _FakeBoundSessionQueueService:
    def __init__(self, handle: AutomationExecutionHandle | None = None) -> None:
        self._handle = handle
        self.materialize_calls: list[tuple[str, str]] = []
        self.deleted_project_ids: list[str] = []

    def materialize_execution(
        self,
        *,
        project: object,
        reason: str,
    ) -> AutomationExecutionHandle | None:
        automation_project_id = getattr(project, "automation_project_id")
        self.materialize_calls.append((cast(str, automation_project_id), reason))
        return self._handle

    def delete_project_queue(self, automation_project_id: str) -> None:
        self.deleted_project_ids.append(automation_project_id)


class _FakeFeishuBindingService:
    def validate_binding(self, binding: object) -> object:
        return binding


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


def _build_service(
    tmp_path: Path,
    *,
    bound_session_queue_service: _FakeBoundSessionQueueService | None = None,
    feishu_binding_service: object | None = None,
) -> tuple[AutomationService, _FakeRunManager, SessionService]:
    db_path = tmp_path / "automation.db"
    run_manager = _FakeRunManager()
    session_service = _build_session_service(db_path)
    workspace_service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = workspace_service.create_workspace(
        workspace_id="default",
        root_path=tmp_path,
    )
    service = AutomationService(
        repository=AutomationProjectRepository(db_path),
        event_repository=AutomationEventRepository(db_path),
        session_service=session_service,
        run_service=cast(RunManager, run_manager),
        feishu_binding_service=cast(
            AutomationFeishuBindingService | None,
            feishu_binding_service,
        ),
        bound_session_queue_service=cast(
            AutomationBoundSessionQueueService | None,
            bound_session_queue_service,
        ),
        workspace_service=workspace_service,
    )
    return service, run_manager, session_service


def test_create_project_sets_next_run_at_for_cron(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)

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

    assert created.trigger_id == f"schedule-{created.automation_project_id}"
    assert created.next_run_at is not None
    assert created.status.value == "enabled"


def test_run_now_creates_automation_session_and_starts_run(tmp_path: Path) -> None:
    service, run_manager, _ = _build_service(tmp_path)
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
    assert result["run_id"] == "run-1"
    assert result["queued"] is False
    assert result["reused_bound_session"] is False
    assert len(sessions) == 1
    session_payload = cast(dict[str, object], sessions[0])
    metadata = cast(dict[str, str], session_payload["metadata"])
    assert session_payload["workspace_id"] == "default"
    assert session_payload["project_kind"] == "automation"
    assert session_payload["project_id"] == created.automation_project_id
    assert metadata["automation_reason"] == "manual"
    assert "automation_trigger_event_id" in metadata
    assert len(run_manager.create_calls) == 1
    assert run_manager.started_run_ids == ["run-1"]
    assert (
        getattr(run_manager.create_calls[0], "intent")
        == "触发定时任务 “nightly-report”：\nDraft a nightly report."
    )


def test_process_due_projects_runs_one_shot_once_and_disables_it(
    tmp_path: Path,
) -> None:
    service, run_manager, _ = _build_service(tmp_path)
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
    assert (
        getattr(run_manager.create_calls[0], "intent")
        == "触发定时任务 “one-shot-report”：\nRun once."
    )


def test_process_due_projects_skips_invalid_persisted_projects(
    tmp_path: Path,
) -> None:
    service, run_manager, _ = _build_service(tmp_path)
    run_at = datetime.now(tz=UTC) + timedelta(minutes=5)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="healthy-one-shot",
            workspace_id="default",
            prompt="Run once.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            run_at=run_at,
            timezone="UTC",
        )
    )
    invalid = service.create_project(
        AutomationProjectCreateInput(
            name="invalid-one-shot",
            workspace_id="default",
            prompt="This row will be corrupted.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            run_at=run_at,
            timezone="UTC",
        )
    )
    connection = sqlite3.connect(tmp_path / "automation.db")
    connection.execute(
        """
        UPDATE automation_projects
        SET workspace_id=?
        WHERE automation_project_id=?
        """,
        ("None", invalid.automation_project_id),
    )
    connection.commit()
    connection.close()

    processed = service.process_due_projects(now=run_at + timedelta(minutes=1))

    assert processed == (created.automation_project_id,)
    assert run_manager.started_run_ids == ["run-1"]
    with pytest.raises(KeyError):
        service.get_project(invalid.automation_project_id)


def test_enable_project_recomputes_schedule_for_manual_run(tmp_path: Path) -> None:
    service, run_manager, _ = _build_service(tmp_path)
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
    result = service.run_now(created.automation_project_id)

    assert enabled.status.value == "enabled"
    assert enabled.next_run_at is not None
    assert result["automation_project_id"] == created.automation_project_id
    assert result["reused_bound_session"] is False
    assert run_manager.started_run_ids == ["run-1"]


def test_run_now_reuses_bound_session_without_creating_new_session(
    tmp_path: Path,
) -> None:
    bound_queue_service = _FakeBoundSessionQueueService(
        AutomationExecutionHandle(
            session_id="bound-session-1",
            run_id="bound-run-1",
            queued=False,
        )
    )
    service, run_manager, session_service = _build_service(
        tmp_path,
        bound_session_queue_service=bound_queue_service,
        feishu_binding_service=_FakeFeishuBindingService(),
    )
    _ = session_service.create_session(
        session_id="bound-session-1",
        workspace_id="default",
        metadata={"title": "Bound IM Session"},
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="nightly-report",
            workspace_id="default",
            prompt="Draft a nightly report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            delivery_binding=AutomationFeishuBinding(
                trigger_id="trg_feishu",
                tenant_key="tenant-1",
                chat_id="oc_123",
                session_id="bound-session-1",
                chat_type="group",
                source_label="Release Updates",
            ),
        )
    )

    result = service.run_now(created.automation_project_id)
    sessions = service.list_project_sessions(created.automation_project_id)

    assert result == {
        "automation_project_id": created.automation_project_id,
        "session_id": "bound-session-1",
        "run_id": "bound-run-1",
        "queued": False,
        "reused_bound_session": True,
    }
    assert bound_queue_service.materialize_calls == [
        (created.automation_project_id, "manual")
    ]
    assert run_manager.create_calls == []
    assert len(sessions) == 1
    session_payload = cast(dict[str, object], sessions[0])
    assert session_payload["session_id"] == "bound-session-1"


def test_run_now_fails_when_bound_session_execution_errors(tmp_path: Path) -> None:
    class _FailingBoundSessionQueueService(_FakeBoundSessionQueueService):
        def materialize_execution(
            self,
            *,
            project: object,
            reason: str,
        ) -> AutomationExecutionHandle | None:
            automation_project_id = getattr(project, "automation_project_id")
            self.materialize_calls.append((cast(str, automation_project_id), reason))
            raise RuntimeError("missing_bound_session:session-im-1")

    bound_queue_service = _FailingBoundSessionQueueService()
    service, run_manager, _session_service = _build_service(
        tmp_path,
        bound_session_queue_service=bound_queue_service,
        feishu_binding_service=_FakeFeishuBindingService(),
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="nightly-report",
            workspace_id="default",
            prompt="Draft a nightly report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            delivery_binding=AutomationFeishuBinding(
                trigger_id="trg_feishu",
                tenant_key="tenant-1",
                chat_id="oc_123",
                session_id="session-im-1",
                chat_type="group",
                source_label="Release Updates",
            ),
        )
    )

    try:
        _ = service.run_now(created.automation_project_id)
    except RuntimeError as exc:
        assert "missing_bound_session:session-im-1" in str(exc)
    else:
        raise AssertionError("Expected bound session error to propagate")
    updated = service.get_project(created.automation_project_id)
    assert updated.last_error == "missing_bound_session:session-im-1"
    assert run_manager.create_calls == []


def test_create_project_rejects_unknown_workspace(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)

    with pytest.raises(ValueError, match="Unknown workspace: missing"):
        service.create_project(
            AutomationProjectCreateInput(
                name="daily-briefing",
                workspace_id="missing",
                prompt="Summarize the day.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 9 * * 1-5",
                timezone="UTC",
            )
        )


def test_update_project_rejects_unknown_workspace(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)
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

    with pytest.raises(ValueError, match="Unknown workspace: missing"):
        service.update_project(
            created.automation_project_id,
            AutomationProjectUpdateInput(workspace_id="missing"),
        )


def test_enable_project_rejects_unknown_workspace_on_persisted_record(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="daily-briefing",
            workspace_id="default",
            prompt="Summarize the day.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * 1-5",
            timezone="UTC",
            enabled=False,
        )
    )
    persisted = service.get_project(created.automation_project_id)
    _ = service._repository.update(
        persisted.model_copy(update={"workspace_id": "missing"})
    )

    with pytest.raises(ValueError, match="Unknown workspace: missing"):
        service.set_project_status(
            created.automation_project_id,
            status=AutomationProjectStatus.ENABLED,
        )
