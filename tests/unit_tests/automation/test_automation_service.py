from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Callable, cast

import pytest
from pydantic import ValidationError

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
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
    AutomationIntervalUnit,
    AutomationProjectCreateInput,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationRunConfig,
    AutomationScheduleMode,
    AutomationXiaolubanBinding,
)
from relay_teams.agents.orchestration.settings_models import (
    OrchestrationPreset,
    OrchestrationSettings,
)
from relay_teams.automation.automation_repository import AutomationProjectRepository
import relay_teams.automation.automation_service as automation_service_module
from relay_teams.automation.feishu_binding_service import (
    AutomationFeishuBindingService,
)
from relay_teams.automation.xiaoluban_binding_service import (
    AutomationXiaolubanBindingService,
)
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressResult,
    GatewaySessionIngressStatus,
)
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.roles import RoleRegistry
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_models import ProjectKind, SessionMode
from relay_teams.sessions.session_service import SessionService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.workspace import WorkspaceRepository, WorkspaceService


class _FakeRunService:
    def __init__(self) -> None:
        self.create_calls: list[object] = []
        self.started_run_ids: list[str] = []

    def create_run(self, intent: object) -> tuple[str, str]:
        session_id = getattr(intent, "session_id")
        self.create_calls.append(intent)
        return (f"run-{len(self.create_calls)}", cast(str, session_id))

    async def create_run_async(self, intent: object) -> tuple[str, str]:
        return self.create_run(intent)

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)

    async def ensure_run_started_async(self, run_id: str) -> None:
        self.ensure_run_started(run_id)


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

    async def materialize_execution(
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

    def list_candidates(self) -> tuple[object, ...]:
        return ()


class _FakeXiaolubanBindingService:
    def __init__(self) -> None:
        self.reject_bindings = False
        self.validate_calls: list[object] = []

    def validate_binding(self, binding: object) -> object:
        self.validate_calls.append(binding)
        if self.reject_bindings:
            raise ValueError("Xiaoluban account cannot receive delivery")
        return binding

    def list_candidates(self) -> tuple[object, ...]:
        return ()


class _FakeDeliveryService:
    def __init__(self, *, has_project_deliveries: bool = False) -> None:
        self._has_project_deliveries = has_project_deliveries
        self.deleted_project_ids: list[str] = []
        self.register_calls: list[tuple[str, str, str, str]] = []

    def has_project_deliveries(self, automation_project_id: str) -> bool:
        return self._has_project_deliveries

    def delete_project_deliveries(self, automation_project_id: str) -> None:
        self.deleted_project_ids.append(automation_project_id)

    def register_run(
        self,
        *,
        project: object,
        session_id: str,
        run_id: str,
        reason: str,
    ) -> object:
        automation_project_id = getattr(project, "automation_project_id")
        self.register_calls.append(
            (cast(str, automation_project_id), session_id, run_id, reason)
        )
        return object()


class _FakeSessionIngressService:
    def __init__(self, *, run_id: str | None = "ingress-run-1") -> None:
        self._run_id = run_id
        self.requests: list[object] = []

    async def require_started_async(
        self,
        request: object,
    ) -> GatewaySessionIngressResult:
        self.requests.append(request)
        return GatewaySessionIngressResult(
            status=GatewaySessionIngressStatus.STARTED,
            session_id="ingress-session-1",
            run_id=self._run_id,
        )


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
    xiaoluban_binding_service: object | None = None,
    session_ingress_service: object | None = None,
    role_registry: _FakeRoleRegistry | None = None,
    get_role_registry: Callable[[], _FakeRoleRegistry | None] | None = None,
    orchestration_settings_service: _FakeOrchestrationSettingsService | None = None,
) -> tuple[
    automation_service_module.AutomationService, _FakeRunService, SessionService
]:
    db_path = tmp_path / "automation.db"
    run_service = _FakeRunService()
    session_service = _build_session_service(db_path)
    workspace_service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = workspace_service.create_workspace(
        workspace_id="default",
        root_path=tmp_path,
    )
    service = automation_service_module.AutomationService(
        repository=AutomationProjectRepository(db_path),
        event_repository=AutomationEventRepository(db_path),
        session_service=session_service,
        run_service=cast(SessionRunService, run_service),
        feishu_binding_service=cast(
            AutomationFeishuBindingService | None,
            feishu_binding_service,
        ),
        xiaoluban_binding_service=cast(
            AutomationXiaolubanBindingService | None,
            xiaoluban_binding_service,
        ),
        delivery_service=cast(AutomationDeliveryService | None, delivery_service),
        bound_session_queue_service=cast(
            AutomationBoundSessionQueueService | None,
            bound_session_queue_service,
        ),
        workspace_service=workspace_service,
        session_ingress_service=cast(
            automation_service_module.GatewaySessionIngressService | None,
            session_ingress_service,
        ),
        role_registry=cast(RoleRegistry | None, role_registry),
        get_role_registry=cast(
            Callable[[], RoleRegistry | None] | None, get_role_registry
        ),
        orchestration_settings_service=cast(
            OrchestrationSettingsService | None,
            orchestration_settings_service,
        ),
    )
    return service, run_service, session_service


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
    service, run_service, _ = _build_service(tmp_path)
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
    assert len(run_service.create_calls) == 1
    assert run_service.started_run_ids == ["run-1"]
    assert (
        getattr(run_service.create_calls[0], "intent")
        == "自动化项目“nightly-report”已由系统触发进入本次执行。\n"
        "不要创建、启动或安排新的定时任务；定时调度由后台负责。"
        "请直接完成以下任务：\n"
        "Draft a nightly report."
    )


def test_async_run_now_creates_automation_session_and_starts_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, run_service, session_service = _build_service(tmp_path)

    def fail_sync_create_session(**_kwargs: object) -> object:
        raise AssertionError("async automation run path used sync session creation")

    monkeypatch.setattr(session_service, "create_session", fail_sync_create_session)

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="nightly-report",
                workspace_id="default",
                prompt="Draft a nightly report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
            )
        )

        result = await service.run_now_async(created.automation_project_id)
        sessions = await service.list_project_sessions_async(
            created.automation_project_id
        )

        assert result["automation_project_id"] == created.automation_project_id
        assert result["run_id"] == "run-1"
        assert result["queued"] is False
        assert len(sessions) == 1
        session_payload = cast(dict[str, object], sessions[0])
        metadata = cast(dict[str, str], session_payload["metadata"])
        assert session_payload["project_id"] == created.automation_project_id
        assert metadata["automation_reason"] == "manual"
        assert len(run_service.create_calls) == 1
        assert run_service.started_run_ids == ["run-1"]

    asyncio.run(exercise())


def test_async_project_queries_return_repository_records(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="query-report",
            workspace_id="default",
            prompt="Draft a query report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
        )
    )

    async def exercise() -> None:
        records = await service.list_projects_async()
        loaded = await service.get_project_async(created.automation_project_id)

        assert [record.automation_project_id for record in records] == [
            created.automation_project_id
        ]
        assert loaded.name == "query-report"

    asyncio.run(exercise())


def test_async_run_now_offloads_delivery_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery_service = _FakeDeliveryService()
    service, _, _ = _build_service(
        tmp_path,
        delivery_service=delivery_service,
        xiaoluban_binding_service=_FakeXiaolubanBindingService(),
    )
    offloaded_functions: list[str] = []

    async def fake_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        offloaded_functions.append(getattr(function, "__name__", ""))
        return function(*args, **kwargs)

    monkeypatch.setattr(automation_service_module.asyncio, "to_thread", fake_to_thread)

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="delivery-report",
                workspace_id="default",
                prompt="Draft a delivery report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                delivery_binding=AutomationXiaolubanBinding(
                    account_id="account-1",
                    display_name="Xiaoluban",
                    derived_uid="uid-1",
                    source_label="Xiaoluban Account",
                ),
            )
        )

        result = await service.run_now_async(created.automation_project_id)

        assert "register_run" in offloaded_functions
        assert result["run_id"] == "run-1"
        assert delivery_service.register_calls == [
            (created.automation_project_id, result["session_id"], "run-1", "manual")
        ]

    asyncio.run(exercise())


def test_async_run_now_uses_session_ingress_service(tmp_path: Path) -> None:
    session_ingress_service = _FakeSessionIngressService(run_id="ingress-run-1")
    service, run_service, _ = _build_service(
        tmp_path,
        session_ingress_service=session_ingress_service,
    )

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="ingress-report",
                workspace_id="default",
                prompt="Draft an ingress report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
            )
        )

        result = await service.run_now_async(created.automation_project_id)

        assert result["run_id"] == "ingress-run-1"
        assert run_service.create_calls == []
        assert len(session_ingress_service.requests) == 1
        request = session_ingress_service.requests[0]
        assert (
            getattr(request, "busy_policy")
            == automation_service_module.GatewaySessionIngressBusyPolicy.START_IF_IDLE
        )

    asyncio.run(exercise())


def test_async_run_now_finishes_startup_when_caller_is_cancelled(
    tmp_path: Path,
) -> None:
    service, run_service, _ = _build_service(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(run_id: str) -> None:
        started.set()
        await release.wait()
        run_service.ensure_run_started(run_id)

    run_service.ensure_run_started_async = wait_for_release

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="cancelled-report",
                workspace_id="default",
                prompt="Draft a report after caller cancellation.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
            )
        )
        task = asyncio.create_task(service.run_now_async(created.automation_project_id))

        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        release.set()

        with pytest.raises(asyncio.CancelledError):
            _ = await task

        updated = await service.get_project_async(created.automation_project_id)
        assert updated.last_session_id is not None
        assert updated.last_run_started_at is not None
        assert len(run_service.create_calls) == 1
        assert run_service.started_run_ids == ["run-1"]

    asyncio.run(exercise())


def test_async_run_now_logs_startup_failure_when_cancelled_caller_waits(
    tmp_path: Path,
) -> None:
    service, run_service, _ = _build_service(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_after_release(run_id: str) -> None:
        _ = run_id
        started.set()
        await release.wait()
        raise RuntimeError("startup exploded")

    run_service.ensure_run_started_async = fail_after_release

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="cancelled-failure-report",
                workspace_id="default",
                prompt="Draft a report after failed startup.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
            )
        )
        task = asyncio.create_task(service.run_now_async(created.automation_project_id))

        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        release.set()

        with pytest.raises(asyncio.CancelledError):
            _ = await task

        updated = await service.get_project_async(created.automation_project_id)
        assert updated.last_error == "startup exploded"
        assert len(run_service.create_calls) == 1
        assert run_service.started_run_ids == []

    asyncio.run(exercise())


def test_async_run_now_offloads_bound_session_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound_queue_service = _FakeBoundSessionQueueService(
        AutomationExecutionHandle(
            session_id="bound-session-1",
            run_id="bound-run-1",
            queued=False,
            reused_bound_session=False,
        )
    )
    service, run_service, _ = _build_service(
        tmp_path,
        bound_session_queue_service=bound_queue_service,
        feishu_binding_service=_FakeFeishuBindingService(),
    )
    offloaded_functions: list[str] = []

    async def fake_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        offloaded_functions.append(getattr(function, "__name__", ""))
        return function(*args, **kwargs)

    monkeypatch.setattr(automation_service_module.asyncio, "to_thread", fake_to_thread)

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="nightly-report",
                workspace_id="default",
                prompt="Draft a nightly report.",
                schedule_mode=AutomationScheduleMode.ONE_SHOT,
                run_at=datetime(2026, 1, 1, tzinfo=UTC),
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

        result = await service.run_now_async(created.automation_project_id)
        updated = await service.get_project_async(created.automation_project_id)

        assert "_materialize_bound_session_execution" not in offloaded_functions
        assert result["session_id"] == "bound-session-1"
        assert result["run_id"] == "bound-run-1"
        assert result["reused_bound_session"] is True
        assert updated.status == AutomationProjectStatus.DISABLED
        assert bound_queue_service.materialize_calls == [
            (created.automation_project_id, "manual")
        ]
        assert run_service.create_calls == []

    asyncio.run(exercise())


def test_async_binding_lists_offload_sync_binding_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        feishu_binding_service=_FakeFeishuBindingService(),
        xiaoluban_binding_service=_FakeXiaolubanBindingService(),
    )
    offloaded_functions: list[str] = []

    async def fake_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        offloaded_functions.append(getattr(function, "__name__", ""))
        return function(*args, **kwargs)

    monkeypatch.setattr(automation_service_module.asyncio, "to_thread", fake_to_thread)

    async def exercise() -> None:
        feishu_bindings = await service.list_feishu_bindings_async()
        delivery_bindings = await service.list_delivery_bindings_async()

        assert feishu_bindings == ()
        assert delivery_bindings == ()
        assert offloaded_functions == [
            "list_feishu_bindings",
            "list_delivery_bindings",
        ]

    asyncio.run(exercise())


def test_async_project_mutations_offload_delivery_binding_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        xiaoluban_binding_service=_FakeXiaolubanBindingService(),
    )
    offloaded_functions: list[str] = []

    async def fake_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        offloaded_functions.append(getattr(function, "__name__", ""))
        return function(*args, **kwargs)

    monkeypatch.setattr(automation_service_module.asyncio, "to_thread", fake_to_thread)

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="delivery-report",
                workspace_id="default",
                prompt="Draft a delivery report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
                delivery_binding=AutomationXiaolubanBinding(
                    account_id="account-1",
                    display_name="Xiaoluban",
                    derived_uid="uid-1",
                    source_label="Xiaoluban Account",
                ),
            )
        )
        updated = await service.update_project_async(
            created.automation_project_id,
            AutomationProjectUpdateInput(
                delivery_binding=AutomationXiaolubanBinding(
                    account_id="account-2",
                    display_name="Xiaoluban",
                    derived_uid="uid-2",
                    source_label="Xiaoluban Account",
                ),
            ),
        )

        assert created.delivery_binding is not None
        assert updated.delivery_binding is not None
        assert offloaded_functions.count("_resolve_delivery_binding") == 2

    asyncio.run(exercise())


def test_list_project_sessions_async_offloads_session_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, session_service = _build_service(tmp_path)
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
    _ = session_service.create_session(
        session_id="session-automation",
        workspace_id="default",
        metadata={"title": "Automation Run"},
        project_kind=ProjectKind.AUTOMATION,
        project_id=created.automation_project_id,
    )
    offloaded_functions: list[str] = []

    async def fake_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        offloaded_functions.append(getattr(function, "__name__", ""))
        return function(*args, **kwargs)

    monkeypatch.setattr(automation_service_module.asyncio, "to_thread", fake_to_thread)

    async def exercise() -> None:
        sessions = await service.list_project_sessions_async(
            created.automation_project_id
        )

        assert offloaded_functions == ["_list_project_sessions_for_record"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "session-automation"

    asyncio.run(exercise())


def test_async_project_mutations_validate_workspace_without_sync_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = _build_service(tmp_path)
    existing = service.create_project(
        AutomationProjectCreateInput(
            name="disabled-report",
            workspace_id="default",
            prompt="Draft a disabled report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            enabled=False,
        )
    )

    def fail_sync_require_workspace(
        self: WorkspaceService,
        workspace_id: str,
    ) -> object:
        _ = (self, workspace_id)
        raise AssertionError("async automation API used sync workspace validation")

    monkeypatch.setattr(
        WorkspaceService, "require_workspace", fail_sync_require_workspace
    )

    async def exercise() -> None:
        created = await service.create_project_async(
            AutomationProjectCreateInput(
                name="async-report",
                workspace_id="default",
                prompt="Draft an async report.",
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 1 * * *",
                timezone="UTC",
            )
        )
        updated = await service.update_project_async(
            existing.automation_project_id,
            AutomationProjectUpdateInput(prompt="Draft the updated report."),
        )
        enabled = await service.set_project_status_async(
            existing.automation_project_id,
            AutomationProjectStatus.ENABLED,
        )

        assert created.workspace_id == "default"
        assert updated.prompt == "Draft the updated report."
        assert enabled.status == AutomationProjectStatus.ENABLED

    asyncio.run(exercise())


def test_update_project_async_handles_schedule_and_delivery_changes(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(
        tmp_path,
        xiaoluban_binding_service=_FakeXiaolubanBindingService(),
    )
    existing = service.create_project(
        AutomationProjectCreateInput(
            name="schedule-report",
            workspace_id="default",
            prompt="Draft a scheduled report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
        )
    )
    run_at = datetime(2026, 1, 1, tzinfo=UTC)

    async def exercise() -> None:
        one_shot = await service.update_project_async(
            existing.automation_project_id,
            AutomationProjectUpdateInput(
                schedule_mode=AutomationScheduleMode.ONE_SHOT,
                run_at=run_at,
                delivery_binding=AutomationXiaolubanBinding(
                    account_id="account-1",
                    display_name="Xiaoluban",
                    derived_uid="uid-1",
                    source_label="Xiaoluban Account",
                ),
            ),
        )
        back_to_cron = await service.update_project_async(
            existing.automation_project_id,
            AutomationProjectUpdateInput(
                schedule_mode=AutomationScheduleMode.CRON,
                cron_expression="0 2 * * *",
            ),
        )

        assert one_shot.schedule_mode == AutomationScheduleMode.ONE_SHOT
        assert one_shot.cron_expression is None
        assert one_shot.delivery_binding is not None
        assert back_to_cron.schedule_mode == AutomationScheduleMode.CRON
        assert back_to_cron.run_at is None
        assert back_to_cron.cron_expression == "0 2 * * *"

    asyncio.run(exercise())


def test_delete_project_async_cascades_dependent_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery_service = _FakeDeliveryService(has_project_deliveries=True)
    bound_queue_service = _FakeBoundSessionQueueService(has_project_queue=True)
    service, _, _ = _build_service(
        tmp_path,
        delivery_service=delivery_service,
        bound_session_queue_service=bound_queue_service,
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="delete-report",
            workspace_id="default",
            prompt="Draft a deleted report.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 1 * * *",
            timezone="UTC",
            enabled=False,
        )
    )
    offloaded_functions: list[str] = []

    async def fake_to_thread(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        offloaded_functions.append(getattr(function, "__name__", ""))
        return function(*args, **kwargs)

    monkeypatch.setattr(automation_service_module.asyncio, "to_thread", fake_to_thread)

    async def exercise() -> None:
        await service.delete_project_async(created.automation_project_id, cascade=True)

        assert offloaded_functions == [
            "has_project_deliveries",
            "delete_project_deliveries",
            "delete_project_queue",
        ]
        assert delivery_service.deleted_project_ids == [created.automation_project_id]
        assert bound_queue_service.deleted_project_ids == [
            created.automation_project_id
        ]
        with pytest.raises(KeyError):
            await service.get_project_async(created.automation_project_id)

    asyncio.run(exercise())


def test_process_due_projects_async_runs_due_projects(tmp_path: Path) -> None:
    service, run_service, _ = _build_service(tmp_path)
    run_at = datetime.now(tz=UTC) + timedelta(minutes=5)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="due-report",
            workspace_id="default",
            prompt="Run the due report.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            run_at=run_at,
            timezone="UTC",
        )
    )

    async def exercise() -> None:
        processed = await service.process_due_projects_async(
            now=run_at + timedelta(minutes=1)
        )
        updated = await service.get_project_async(created.automation_project_id)

        assert processed == (created.automation_project_id,)
        assert updated.status == AutomationProjectStatus.DISABLED
        assert run_service.started_run_ids == ["run-1"]

    asyncio.run(exercise())


def test_async_run_failure_updates_project_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, run_service, _ = _build_service(tmp_path)
    run_at = datetime(2026, 1, 1, tzinfo=UTC)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="failing-report",
            workspace_id="default",
            prompt="Run the failing report.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            run_at=run_at,
            timezone="UTC",
        )
    )

    async def fail_create_run_async(intent: object) -> tuple[str, str]:
        _ = intent
        raise RuntimeError("run failed")

    monkeypatch.setattr(run_service, "create_run_async", fail_create_run_async)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="run failed"):
            await service.run_now_async(created.automation_project_id)
        updated = await service.get_project_async(created.automation_project_id)

        assert updated.status == AutomationProjectStatus.DISABLED
        assert updated.last_error == "run failed"
        assert updated.next_run_at is None

    asyncio.run(exercise())


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
    service, run_service, _ = _build_service(tmp_path)
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
    assert run_service.started_run_ids == ["run-1"]
    assert (
        getattr(run_service.create_calls[0], "intent")
        == "自动化项目“one-shot-report”已由系统触发进入本次执行。\n"
        "不要创建、启动或安排新的定时任务；定时调度由后台负责。"
        "请直接完成以下任务：\n"
        "Run once."
    )


def test_interval_project_computes_and_advances_next_run(tmp_path: Path) -> None:
    service, run_service, _ = _build_service(tmp_path)
    now = datetime.now(tz=UTC)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="interval-report",
            workspace_id="default",
            prompt="Run repeatedly.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=15,
            interval_unit=AutomationIntervalUnit.MINUTES,
            timezone="UTC",
        )
    )

    assert created.next_run_at is not None
    assert created.next_run_at > now
    assert created.next_run_at <= now + timedelta(minutes=16)

    processed = service.process_due_projects(now=created.next_run_at)
    updated = service.get_project(created.automation_project_id)

    assert processed == (created.automation_project_id,)
    assert updated.status == AutomationProjectStatus.ENABLED
    assert updated.next_run_at == created.next_run_at + timedelta(minutes=15)
    assert run_service.started_run_ids == ["run-1"]


def test_manual_interval_run_preserves_schedule_cursor(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="manual-interval-report",
            workspace_id="default",
            prompt="Run repeatedly.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=2,
            interval_unit=AutomationIntervalUnit.HOURS,
            timezone="UTC",
        )
    )

    _ = service.run_now(created.automation_project_id)
    updated = service.get_project(created.automation_project_id)

    assert updated.next_run_at == created.next_run_at


def test_update_project_switches_interval_to_cron_and_one_shot(
    tmp_path: Path,
) -> None:
    service, _, _ = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="switchable-interval",
            workspace_id="default",
            prompt="Run repeatedly.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=2,
            interval_unit=AutomationIntervalUnit.HOURS,
            timezone="UTC",
        )
    )

    cron_updated = service.update_project(
        created.automation_project_id,
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * *",
        ),
    )
    one_shot_at = datetime.now(tz=UTC) + timedelta(days=1)
    one_shot_updated = service.update_project(
        created.automation_project_id,
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            run_at=one_shot_at,
        ),
    )

    assert cron_updated.schedule_mode == AutomationScheduleMode.CRON
    assert cron_updated.interval_every is None
    assert cron_updated.interval_unit is None
    assert cron_updated.run_at is None
    assert one_shot_updated.schedule_mode == AutomationScheduleMode.ONE_SHOT
    assert one_shot_updated.cron_expression is None
    assert one_shot_updated.interval_every is None
    assert one_shot_updated.interval_unit is None
    assert one_shot_updated.run_at == one_shot_at


def test_update_project_async_switches_cron_to_interval(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="async-switchable-cron",
            workspace_id="default",
            prompt="Run daily.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * *",
            timezone="UTC",
        )
    )

    async def exercise() -> None:
        updated = await service.update_project_async(
            created.automation_project_id,
            AutomationProjectUpdateInput(
                schedule_mode=AutomationScheduleMode.INTERVAL,
                interval_every=30,
                interval_unit=AutomationIntervalUnit.MINUTES,
            ),
        )
        assert updated.schedule_mode == AutomationScheduleMode.INTERVAL
        assert updated.cron_expression is None
        assert updated.run_at is None
        assert updated.interval_every == 30
        assert updated.interval_unit == AutomationIntervalUnit.MINUTES

    asyncio.run(exercise())


def test_interval_day_schedule_advances_after_scheduled_fire(tmp_path: Path) -> None:
    service, _, _ = _build_service(tmp_path)
    created = service.create_project(
        AutomationProjectCreateInput(
            name="daily-interval-report",
            workspace_id="default",
            prompt="Run every day.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=1,
            interval_unit=AutomationIntervalUnit.DAYS,
            timezone="UTC",
        )
    )

    assert created.next_run_at is not None
    processed = service.process_due_projects(now=created.next_run_at)
    updated = service.get_project(created.automation_project_id)

    assert processed == (created.automation_project_id,)
    assert updated.next_run_at == created.next_run_at + timedelta(days=1)


def test_next_run_at_rejects_incomplete_interval_fields() -> None:
    with pytest.raises(ValueError, match="interval_every is required"):
        automation_service_module._next_run_at(
            schedule_mode=AutomationScheduleMode.INTERVAL,
            cron_expression=None,
            interval_every=None,
            interval_unit=AutomationIntervalUnit.MINUTES,
            run_at=None,
            timezone_name="UTC",
            after=datetime.now(tz=UTC),
        )
    with pytest.raises(ValueError, match="interval_unit is required"):
        automation_service_module._next_run_at(
            schedule_mode=AutomationScheduleMode.INTERVAL,
            cron_expression=None,
            interval_every=1,
            interval_unit=None,
            run_at=None,
            timezone_name="UTC",
            after=datetime.now(tz=UTC),
        )


def test_interval_schedule_validation_rejects_cron_expression() -> None:
    with pytest.raises(ValidationError, match="cron_expression is not supported"):
        AutomationProjectCreateInput(
            name="invalid-interval-report",
            workspace_id="default",
            prompt="Run repeatedly.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=1,
            interval_unit=AutomationIntervalUnit.DAYS,
            cron_expression="0 9 * * *",
            timezone="UTC",
        )


def test_schedule_create_input_rejects_invalid_field_combinations() -> None:
    with pytest.raises(ValidationError, match="cron_expression must use five fields"):
        AutomationProjectCreateInput(
            name="bad-cron",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * *",
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="interval fields are not supported"):
        AutomationProjectCreateInput(
            name="cron-with-interval",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * *",
            interval_every=1,
            interval_unit=AutomationIntervalUnit.HOURS,
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="interval_every is required"):
        AutomationProjectCreateInput(
            name="missing-interval-every",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_unit=AutomationIntervalUnit.HOURS,
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="interval_unit is required"):
        AutomationProjectCreateInput(
            name="missing-interval-unit",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=1,
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="run_at is not supported"):
        AutomationProjectCreateInput(
            name="interval-with-run-at",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.INTERVAL,
            interval_every=1,
            interval_unit=AutomationIntervalUnit.HOURS,
            run_at=datetime.now(tz=UTC),
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="cron_expression is not supported"):
        AutomationProjectCreateInput(
            name="one-shot-with-cron",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            cron_expression="0 9 * * *",
            run_at=datetime.now(tz=UTC),
            timezone="UTC",
        )
    with pytest.raises(ValidationError, match="interval fields are not supported"):
        AutomationProjectCreateInput(
            name="one-shot-with-interval",
            workspace_id="default",
            prompt="Run.",
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            interval_every=1,
            interval_unit=AutomationIntervalUnit.DAYS,
            run_at=datetime.now(tz=UTC),
            timezone="UTC",
        )


def test_schedule_update_input_rejects_invalid_field_combinations() -> None:
    with pytest.raises(ValidationError, match="interval fields are not supported"):
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * *",
            interval_every=1,
        )
    with pytest.raises(ValidationError, match="cron_expression is not supported"):
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.INTERVAL,
            cron_expression="0 9 * * *",
        )
    with pytest.raises(ValidationError, match="run_at is not supported"):
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.INTERVAL,
            run_at=datetime.now(tz=UTC),
        )
    with pytest.raises(ValidationError, match="cron_expression must use five fields"):
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * *",
        )
    with pytest.raises(ValidationError, match="interval fields are not supported"):
        AutomationProjectUpdateInput(
            schedule_mode=AutomationScheduleMode.ONE_SHOT,
            interval_unit=AutomationIntervalUnit.MINUTES,
        )


def test_process_due_projects_skips_invalid_persisted_projects(
    tmp_path: Path,
) -> None:
    service, run_service, _ = _build_service(tmp_path)
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
    assert run_service.started_run_ids == ["run-1"]
    with pytest.raises(KeyError):
        service.get_project(invalid.automation_project_id)


def test_enable_project_recomputes_schedule_for_manual_run(tmp_path: Path) -> None:
    service, run_service, _ = _build_service(tmp_path)
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
    assert run_service.started_run_ids == ["run-1"]


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
    service, run_service, session_service = _build_service(
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
    assert run_service.create_calls == []
    assert len(sessions) == 1
    session_payload = cast(dict[str, object], sessions[0])
    assert session_payload["session_id"] == "bound-session-1"


def test_run_now_fails_when_bound_session_execution_errors(tmp_path: Path) -> None:
    class _FailingBoundSessionQueueService(_FakeBoundSessionQueueService):
        async def materialize_execution(
            self,
            *,
            project: object,
            reason: str,
        ) -> AutomationExecutionHandle | None:
            automation_project_id = getattr(project, "automation_project_id")
            self.materialize_calls.append((cast(str, automation_project_id), reason))
            raise RuntimeError("missing_bound_session:session-im-1")

    bound_queue_service = _FailingBoundSessionQueueService()
    service, run_service, _session_service = _build_service(
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
    assert run_service.create_calls == []


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


def test_update_project_skips_xiaoluban_revalidation_for_unrelated_patch(
    tmp_path: Path,
) -> None:
    binding_service = _FakeXiaolubanBindingService()
    service, _, _ = _build_service(
        tmp_path,
        xiaoluban_binding_service=binding_service,
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="daily-briefing",
            workspace_id="default",
            prompt="Summarize the day.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * 1-5",
            timezone="UTC",
            delivery_binding=AutomationXiaolubanBinding(
                account_id="xlb_1",
                display_name="小鲁班主账号",
                derived_uid="uidself",
                source_label="小鲁班主账号",
            ),
        )
    )
    binding_service.reject_bindings = True

    updated = service.update_project(
        created.automation_project_id,
        AutomationProjectUpdateInput(prompt="Summarize the week."),
    )

    assert updated.prompt == "Summarize the week."
    assert updated.delivery_binding == created.delivery_binding
    assert len(binding_service.validate_calls) == 1


def test_update_project_can_clear_xiaoluban_binding_without_revalidation(
    tmp_path: Path,
) -> None:
    binding_service = _FakeXiaolubanBindingService()
    service, _, _ = _build_service(
        tmp_path,
        xiaoluban_binding_service=binding_service,
    )
    created = service.create_project(
        AutomationProjectCreateInput(
            name="daily-briefing",
            workspace_id="default",
            prompt="Summarize the day.",
            schedule_mode=AutomationScheduleMode.CRON,
            cron_expression="0 9 * * 1-5",
            timezone="UTC",
            delivery_binding=AutomationXiaolubanBinding(
                account_id="xlb_1",
                display_name="小鲁班主账号",
                derived_uid="uidself",
                source_label="小鲁班主账号",
            ),
        )
    )
    binding_service.reject_bindings = True

    updated = service.update_project(
        created.automation_project_id,
        AutomationProjectUpdateInput(delivery_binding=None),
    )

    assert updated.delivery_binding is None
    assert updated.delivery_events == ()
    assert len(binding_service.validate_calls) == 1


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
