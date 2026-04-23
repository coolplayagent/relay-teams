from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Callable, cast

import pytest
from pydantic import ValidationError

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.automation.automation_bound_session_queue_service import (
    AutomationBoundSessionQueueService,
)
from relay_teams.automation.automation_delivery_service import AutomationDeliveryService
from relay_teams.automation.automation_event_repository import (
    AutomationEventRepository,
)
from relay_teams.automation.automation_models import (
    AutomationExecutionHandle,
    AutomationFeishuBinding,
    AutomationProjectCreateInput,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from relay_teams.agents.orchestration.settings_models import (
    OrchestrationPreset,
    OrchestrationSettings,
)
from relay_teams.automation.automation_repository import AutomationProjectRepository
from relay_teams.automation.automation_service import AutomationService
from relay_teams.automation.feishu_binding_service import (
    AutomationFeishuBindingService,
)
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.roles import RoleRegistry
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_models import SessionMode
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
    def __init__(
        self,
        handle: AutomationExecutionHandle | None = None,
        *,
        has_project_queue: bool = False,
    ) -> None:
        self._handle = handle
        self._has_project_queue = has_project_queue
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

    def has_project_queue(self, automation_project_id: str) -> bool:
        return self._has_project_queue

    def delete_project_queue(self, automation_project_id: str) -> None:
        self.deleted_project_ids.append(automation_project_id)


class _FakeFeishuBindingService:
    def validate_binding(self, binding: object) -> object:
        return binding


class _FakeDeliveryService:
    def __init__(self, *, has_project_deliveries: bool = False) -> None:
        self._has_project_deliveries = has_project_deliveries
        self.deleted_project_ids: list[str] = []

    def has_project_deliveries(self, automation_project_id: str) -> bool:
        return self._has_project_deliveries

    def delete_project_deliveries(self, automation_project_id: str) -> None:
        self.deleted_project_ids.append(automation_project_id)


class _FakeRoleRegistry:
    def __init__(
        self, *, valid_role_ids: tuple[str, ...] = ("MainAgent", "Writer")
    ) -> None:
        self._valid_role_ids = valid_role_ids

    def resolve_normal_mode_role_id(self, role_id: str | None) -> str:
        normalized = str(role_id or "").strip()
        if not normalized:
            return "MainAgent"
        if normalized == "Coordinator":
            raise ValueError(
                f"Coordinator role cannot be used in normal mode: {normalized}"
            )
        if normalized not in self._valid_role_ids:
            raise ValueError(f"Unknown normal mode role: {normalized}")
        return normalized


class _FakeOrchestrationSettingsService:
    def __init__(self, *, preset_ids: tuple[str, ...] = ("preset-main",)) -> None:
        presets = tuple(
            OrchestrationPreset(
                preset_id=preset_id,
                name=preset_id,
                role_ids=("Writer",),
                orchestration_prompt="Coordinate the run.",
            )
            for preset_id in preset_ids
        )
        self._settings = OrchestrationSettings(
            default_orchestration_preset_id=preset_ids[0] if preset_ids else "",
            presets=presets,
        )

    def get_orchestration_config(self) -> OrchestrationSettings:
        return self._settings


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
    delivery_service: _FakeDeliveryService | None = None,
    feishu_binding_service: object | None = None,
    role_registry: _FakeRoleRegistry | None = None,
    get_role_registry: Callable[[], _FakeRoleRegistry | None] | None = None,
    orchestration_settings_service: _FakeOrchestrationSettingsService | None = None,
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
        delivery_service=cast(AutomationDeliveryService | None, delivery_service),
        bound_session_queue_service=cast(
            AutomationBoundSessionQueueService | None,
            bound_session_queue_service,
        ),
        workspace_service=workspace_service,
        role_registry=cast(RoleRegistry | None, role_registry),
        get_role_registry=cast(
            Callable[[], RoleRegistry | None] | None, get_role_registry
        ),
        orchestration_settings_service=cast(
            OrchestrationSettingsService | None,
            orchestration_settings_service,
        ),
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


def test_create_project_persists_normal_root_role_id(tmp_path: Path) -> None:
    service, _, _ = _build_service(
        tmp_path,
        role_registry=_FakeRoleRegistry(),
    )

    created = service.create_project(
        AutomationProjectCreateInput(
            name="writer-report",
            workspace_id="default",
            prompt="Draft the report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            run_config=AutomationRunConfig(
                session_mode=SessionMode.NORMAL,
                normal_root_role_id="Writer",
            ),
        )
    )

    assert created.run_config.normal_root_role_id == "Writer"
    assert created.run_config.orchestration_preset_id is None


def test_create_project_rejects_unknown_normal_root_role_id(tmp_path: Path) -> None:
    service, _, _ = _build_service(
        tmp_path,
        role_registry=_FakeRoleRegistry(),
    )

    with pytest.raises(ValueError, match="Unknown normal mode role: UnknownRole"):
        service.create_project(
            AutomationProjectCreateInput(
                name="writer-report",
                workspace_id="default",
                prompt="Draft the report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                run_config=AutomationRunConfig(
                    session_mode=SessionMode.NORMAL,
                    normal_root_role_id="UnknownRole",
                ),
            )
        )


def test_create_project_rejects_orchestration_mode_without_preset(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        orchestration_settings_service=_FakeOrchestrationSettingsService(),
    )

    with pytest.raises(
        ValueError,
        match="orchestration_preset_id is required in orchestration mode",
    ):
        service.create_project(
            AutomationProjectCreateInput(
                name="coordinated-report",
                workspace_id="default",
                prompt="Coordinate the report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                run_config=AutomationRunConfig(
                    session_mode=SessionMode.ORCHESTRATION,
                ),
            )
        )


def test_run_now_stores_session_topology_from_run_config(tmp_path: Path) -> None:
    service, _, _ = _build_service(
        tmp_path,
        role_registry=_FakeRoleRegistry(),
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="writer-report",
            workspace_id="default",
            prompt="Draft the report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            run_config=AutomationRunConfig(
                session_mode=SessionMode.NORMAL,
                normal_root_role_id="Writer",
            ),
        )
    )

    _ = service.run_now(created.automation_project_id)
    sessions = service.list_project_sessions(created.automation_project_id)
    session_payload = cast(dict[str, object], sessions[0])

    assert session_payload["session_mode"] == "normal"
    assert session_payload["normal_root_role_id"] == "Writer"
    assert session_payload["orchestration_preset_id"] is None


def test_create_project_rejects_normal_root_role_without_role_registry(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(tmp_path)

    with pytest.raises(ValueError, match="Role registry is unavailable"):
        service.create_project(
            AutomationProjectCreateInput(
                name="writer-report",
                workspace_id="default",
                prompt="Draft the report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                run_config=AutomationRunConfig(
                    session_mode=SessionMode.NORMAL,
                    normal_root_role_id="Writer",
                ),
            )
        )


def test_create_project_rejects_orchestration_mode_without_settings_service(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(tmp_path)

    with pytest.raises(
        ValueError,
        match="Orchestration settings service is unavailable",
    ):
        service.create_project(
            AutomationProjectCreateInput(
                name="coordinated-report",
                workspace_id="default",
                prompt="Coordinate the report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                run_config=AutomationRunConfig(
                    session_mode=SessionMode.ORCHESTRATION,
                    orchestration_preset_id="preset-main",
                ),
            )
        )


def test_create_project_rejects_unknown_orchestration_preset(tmp_path: Path) -> None:
    service, _, _ = _build_service(
        tmp_path,
        orchestration_settings_service=_FakeOrchestrationSettingsService(),
    )

    with pytest.raises(
        ValueError,
        match="Unknown orchestration preset: preset-missing",
    ):
        service.create_project(
            AutomationProjectCreateInput(
                name="coordinated-report",
                workspace_id="default",
                prompt="Coordinate the report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                run_config=AutomationRunConfig(
                    session_mode=SessionMode.ORCHESTRATION,
                    orchestration_preset_id="preset-missing",
                ),
            )
        )


def test_run_config_execution_coercion_drops_invalid_persisted_normal_role(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        role_registry=_FakeRoleRegistry(),
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="writer-report",
            workspace_id="default",
            prompt="Draft the report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            run_config=AutomationRunConfig(
                session_mode=SessionMode.NORMAL,
                normal_root_role_id="Writer",
            ),
        )
    )

    coerced = service._coerce_run_config_for_execution(
        created.model_copy(
            update={
                "run_config": created.run_config.model_copy(
                    update={"normal_root_role_id": "UnknownRole"}
                )
            }
        )
    )

    assert coerced.normal_root_role_id is None
    assert coerced.orchestration_preset_id is None


def test_create_project_uses_latest_role_registry_after_reload(
    tmp_path: Path,
) -> None:
    role_registry_holder: dict[str, _FakeRoleRegistry | None] = {
        "registry": _FakeRoleRegistry(valid_role_ids=("MainAgent", "Writer"))
    }
    service, _, _ = _build_service(
        tmp_path,
        get_role_registry=lambda: role_registry_holder["registry"],
    )

    role_registry_holder["registry"] = _FakeRoleRegistry(
        valid_role_ids=("MainAgent", "Writer", "Analyst")
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="analyst-report",
            workspace_id="default",
            prompt="Draft the analyst report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            run_config=AutomationRunConfig(
                session_mode=SessionMode.NORMAL,
                normal_root_role_id="Analyst",
            ),
        )
    )

    assert created.run_config.normal_root_role_id == "Analyst"


def test_run_config_execution_coercion_keeps_valid_orchestration_preset(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        orchestration_settings_service=_FakeOrchestrationSettingsService(),
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="coordinated-report",
            workspace_id="default",
            prompt="Coordinate the report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            run_config=AutomationRunConfig(
                session_mode=SessionMode.ORCHESTRATION,
                orchestration_preset_id="preset-main",
            ),
        )
    )

    coerced = service._coerce_run_config_for_execution(created)

    assert coerced.normal_root_role_id is None
    assert coerced.orchestration_preset_id == "preset-main"


def test_run_config_execution_coercion_drops_invalid_persisted_orchestration_preset(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        orchestration_settings_service=_FakeOrchestrationSettingsService(),
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="coordinated-report",
            workspace_id="default",
            prompt="Coordinate the report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            run_config=AutomationRunConfig(
                session_mode=SessionMode.ORCHESTRATION,
                orchestration_preset_id="preset-main",
            ),
        )
    )

    coerced = service._coerce_run_config_for_execution(
        created.model_copy(
            update={
                "run_config": created.run_config.model_copy(
                    update={"orchestration_preset_id": "preset-missing"}
                )
            }
        )
    )

    assert coerced.normal_root_role_id is None
    assert coerced.orchestration_preset_id is None


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


def test_update_project_input_rejects_empty_patch() -> None:
    with pytest.raises(ValidationError, match="update must include at least one field"):
        AutomationProjectUpdateInput()


def test_update_project_input_rejects_cron_run_at_combo() -> None:
    with pytest.raises(
        ValidationError,
        match="run_at is not supported for cron schedules",
    ):
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.CRON,
            run_at=datetime.now(tz=UTC),
        )


def test_delete_enabled_project_requires_force(tmp_path: Path) -> None:
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

    with pytest.raises(
        RuntimeError,
        match="Cannot delete enabled automation project without force",
    ):
        service.delete_project(created.automation_project_id)

    service.delete_project(created.automation_project_id, force=True)

    with pytest.raises(KeyError):
        service.get_project(created.automation_project_id)


def test_delete_project_rejects_related_records_without_cascade(tmp_path: Path) -> None:
    delivery_service = _FakeDeliveryService(has_project_deliveries=True)
    bound_queue_service = _FakeBoundSessionQueueService(has_project_queue=True)
    service, _, _ = _build_service(
        tmp_path,
        delivery_service=delivery_service,
        bound_session_queue_service=bound_queue_service,
    )
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

    with pytest.raises(
        RuntimeError,
        match="Cannot delete automation project without cascade while deliveries or queue records exist",
    ):
        service.delete_project(created.automation_project_id)


def test_delete_project_cascade_cleans_related_records(tmp_path: Path) -> None:
    delivery_service = _FakeDeliveryService(has_project_deliveries=True)
    bound_queue_service = _FakeBoundSessionQueueService(has_project_queue=True)
    service, _, _ = _build_service(
        tmp_path,
        delivery_service=delivery_service,
        bound_session_queue_service=bound_queue_service,
    )
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

    service.delete_project(created.automation_project_id, cascade=True)

    assert delivery_service.deleted_project_ids == [created.automation_project_id]
    assert bound_queue_service.deleted_project_ids == [created.automation_project_id]
    with pytest.raises(KeyError):
        service.get_project(created.automation_project_id)
