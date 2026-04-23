from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.automation.automation_models import (
    AutomationDeliveryEvent,
    AutomationFeishuBinding,
    AutomationFeishuBindingCandidate,
    AutomationProjectCreateInput,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from relay_teams.automation.errors import AutomationProjectNameConflictError
from relay_teams.interfaces.server.deps import get_automation_service
from relay_teams.interfaces.server.routers import automation


class _FakeAutomationService:
    def __init__(self) -> None:
        self.created_payloads: list[AutomationProjectCreateInput] = []
        self.run_calls: list[str] = []
        self.status_calls: list[tuple[str, AutomationProjectStatus]] = []
        self.deleted_calls: list[tuple[str, bool, bool]] = []
        self.updated_payloads: list[tuple[str, AutomationProjectUpdateInput]] = []
        self.delete_error: Exception | None = None
        self.list_feishu_bindings_calls = 0

    def create_project(
        self, req: AutomationProjectCreateInput
    ) -> AutomationProjectRecord:
        self.created_payloads.append(req)
        if req.name == "duplicate-project":
            raise AutomationProjectNameConflictError(
                f"Automation project name already exists: {req.name}"
            )
        return AutomationProjectRecord(
            automation_project_id="aut_created",
            name=req.name,
            display_name=req.display_name or req.name,
            status=(
                AutomationProjectStatus.ENABLED
                if req.enabled
                else AutomationProjectStatus.DISABLED
            ),
            workspace_id=req.workspace_id,
            prompt=req.prompt,
            schedule_mode=req.schedule_mode,
            cron_expression=req.cron_expression,
            run_at=req.run_at,
            timezone=req.timezone,
            run_config=req.run_config,
            delivery_binding=req.delivery_binding,
            delivery_events=req.delivery_events,
            trigger_id="trg_created",
            next_run_at=datetime(2026, 3, 24, 9, 0, tzinfo=UTC)
            if req.enabled
            else None,
        )

    def list_projects(self) -> tuple[AutomationProjectRecord, ...]:
        return (self.get_project("aut_1"),)

    def get_project(self, automation_project_id: str) -> AutomationProjectRecord:
        if automation_project_id != "aut_1":
            raise KeyError(f"Unknown automation_project_id: {automation_project_id}")
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
                session_id="session-im-1",
                chat_type="group",
                source_label="Release Updates",
            ),
            delivery_events=(
                AutomationDeliveryEvent.STARTED,
                AutomationDeliveryEvent.COMPLETED,
                AutomationDeliveryEvent.FAILED,
            ),
            trigger_id="trg_1",
            next_run_at=datetime(2026, 3, 23, 9, 0, tzinfo=UTC),
        )

    def list_feishu_bindings(self) -> tuple[AutomationFeishuBindingCandidate, ...]:
        self.list_feishu_bindings_calls += 1
        return (
            AutomationFeishuBindingCandidate(
                trigger_id="trg_feishu",
                trigger_name="Feishu Main",
                tenant_key="tenant-1",
                chat_id="oc_123",
                chat_type="group",
                source_label="Release Updates",
                session_id="session-im-1",
                session_title="feishu_main - Release Updates",
                updated_at=datetime(2026, 3, 23, 8, 0, tzinfo=UTC),
            ),
        )

    def update_project(
        self, automation_project_id: str, req: AutomationProjectUpdateInput
    ) -> AutomationProjectRecord:
        self.updated_payloads.append((automation_project_id, req))
        return self.get_project(automation_project_id).model_copy(
            update={
                "name": req.name or "daily-briefing",
                "display_name": req.display_name or "Daily Briefing",
            }
        )

    def delete_project(
        self,
        automation_project_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        if automation_project_id != "aut_1":
            raise KeyError(f"Unknown automation_project_id: {automation_project_id}")
        self.deleted_calls.append((automation_project_id, force, cascade))

    def run_now(self, automation_project_id: str) -> dict[str, str | bool | None]:
        self.run_calls.append(automation_project_id)
        return {
            "automation_project_id": automation_project_id,
            "session_id": "session-automation-1",
            "run_id": "run-automation-1",
            "queued": False,
            "reused_bound_session": False,
        }

    def set_project_status(
        self,
        automation_project_id: str,
        status: AutomationProjectStatus,
    ) -> AutomationProjectRecord:
        if automation_project_id == "invalid":
            raise ValueError("Unknown workspace: missing-workspace")
        if automation_project_id != "aut_1":
            raise KeyError(f"Unknown automation_project_id: {automation_project_id}")
        self.status_calls.append((automation_project_id, status))
        return self.get_project(automation_project_id).model_copy(
            update={"status": status}
        )

    def list_project_sessions(
        self, automation_project_id: str
    ) -> tuple[dict[str, object], ...]:
        return (
            {
                "session_id": "session-automation-1",
                "workspace_id": "default",
                "project_kind": "automation",
                "project_id": automation_project_id,
                "metadata": {"title": "Daily Briefing"},
                "session_mode": "normal",
                "orchestration_preset_id": None,
                "started_at": None,
                "can_switch_mode": True,
                "has_active_run": False,
                "active_run_id": None,
                "active_run_status": None,
                "active_run_phase": None,
                "pending_tool_approval_count": 0,
                "created_at": "2026-03-23T00:00:00+00:00",
                "updated_at": "2026-03-23T00:00:00+00:00",
            },
        )


def _client(fake_service: _FakeAutomationService) -> TestClient:
    app = FastAPI()
    app.include_router(automation.router, prefix="/api")
    app.dependency_overrides[get_automation_service] = lambda: fake_service
    return TestClient(app)


def test_create_project_route_returns_created_record() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.post(
        "/api/automation/projects",
        json={
            "name": "daily-briefing",
            "display_name": "Daily Briefing",
            "workspace_id": "default",
            "prompt": "Summarize the day.",
            "schedule_mode": "cron",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["automation_project_id"] == "aut_created"
    assert payload["status"] == "enabled"
    assert fake_service.created_payloads[0].name == "daily-briefing"


def test_create_project_route_maps_name_conflict_to_409() -> None:
    client = _client(_FakeAutomationService())

    response = client.post(
        "/api/automation/projects",
        json={
            "name": "duplicate-project",
            "workspace_id": "default",
            "prompt": "Summarize the day.",
            "schedule_mode": "cron",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_project_route_rejects_none_like_workspace_id() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.post(
        "/api/automation/projects",
        json={
            "name": "daily-briefing",
            "workspace_id": "None",
            "prompt": "Summarize the day.",
            "schedule_mode": "cron",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 422
    assert fake_service.created_payloads == []


def test_list_projects_route_returns_records() -> None:
    client = _client(_FakeAutomationService())

    response = client.get("/api/automation/projects")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["automation_project_id"] == "aut_1"
    assert payload[0]["schedule_mode"] == "cron"
    assert payload[0]["delivery_binding"]["chat_id"] == "oc_123"
    assert payload[0]["delivery_binding"]["session_id"] == "session-im-1"
    assert payload[0]["delivery_events"] == ["started", "completed", "failed"]


def test_list_feishu_bindings_route_returns_candidates() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.get("/api/automation/feishu-bindings")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["trigger_id"] == "trg_feishu"
    assert payload[0]["chat_id"] == "oc_123"
    assert fake_service.list_feishu_bindings_calls == 1


def test_automation_routes_run_service_calls_in_threadpool(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_run_in_threadpool(
        func: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(automation, "run_in_threadpool", fake_run_in_threadpool)
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    requests = [
        client.get("/api/automation/feishu-bindings"),
        client.post(
            "/api/automation/projects",
            json={
                "name": "daily-briefing",
                "workspace_id": "default",
                "prompt": "Summarize the day.",
                "schedule_mode": "cron",
                "cron_expression": "0 9 * * *",
                "timezone": "UTC",
            },
        ),
        client.get("/api/automation/projects"),
        client.get("/api/automation/projects/aut_1"),
        client.patch(
            "/api/automation/projects/aut_1",
            json={"display_name": "Updated Briefing"},
        ),
        client.delete("/api/automation/projects/aut_1"),
        client.post("/api/automation/projects/aut_1:run"),
        client.post("/api/automation/projects/aut_1:enable"),
        client.post("/api/automation/projects/aut_1:disable"),
        client.get("/api/automation/projects/aut_1/sessions"),
    ]

    assert [response.status_code for response in requests] == [200] * len(requests)
    assert [call[0] for call in calls] == [
        "list_feishu_bindings",
        "create_project",
        "list_projects",
        "get_project",
        "update_project",
        "delete_project",
        "run_now",
        "set_project_status",
        "set_project_status",
        "list_project_sessions",
    ]


def test_get_project_route_returns_record() -> None:
    client = _client(_FakeAutomationService())

    response = client.get("/api/automation/projects/aut_1")

    assert response.status_code == 200
    assert response.json()["automation_project_id"] == "aut_1"


def test_get_project_route_rejects_none_like_path_identifier() -> None:
    client = _client(_FakeAutomationService())

    response = client.get("/api/automation/projects/None")

    assert response.status_code == 422


def test_run_project_route_returns_session_id() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.post("/api/automation/projects/aut_1:run")

    assert response.status_code == 200
    assert response.json() == {
        "automation_project_id": "aut_1",
        "session_id": "session-automation-1",
        "run_id": "run-automation-1",
        "queued": False,
        "reused_bound_session": False,
    }
    assert fake_service.run_calls == ["aut_1"]


def test_list_project_sessions_route_returns_project_scoped_sessions() -> None:
    client = _client(_FakeAutomationService())

    response = client.get("/api/automation/projects/aut_1/sessions")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["project_kind"] == "automation"
    assert payload[0]["project_id"] == "aut_1"


def test_enable_project_route_returns_enabled_record() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.post("/api/automation/projects/aut_1:enable")

    assert response.status_code == 200
    assert response.json()["status"] == "enabled"
    assert fake_service.status_calls == [("aut_1", AutomationProjectStatus.ENABLED)]


def test_enable_project_route_maps_validation_error_to_422() -> None:
    client = _client(_FakeAutomationService())

    response = client.post("/api/automation/projects/invalid:enable")

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown workspace: missing-workspace"


def test_disable_project_route_returns_disabled_record() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.post("/api/automation/projects/aut_1:disable")

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert fake_service.status_calls == [("aut_1", AutomationProjectStatus.DISABLED)]


def test_delete_project_route_returns_ok() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.delete("/api/automation/projects/aut_1")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.deleted_calls == [("aut_1", False, False)]


def test_delete_project_route_forwards_force_and_cascade() -> None:
    fake_service = _FakeAutomationService()
    client = _client(fake_service)

    response = client.request(
        "DELETE",
        "/api/automation/projects/aut_1",
        json={"force": True, "cascade": True},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.deleted_calls == [("aut_1", True, True)]


def test_delete_project_route_returns_conflict_for_missing_cascade() -> None:
    fake_service = _FakeAutomationService()
    fake_service.delete_error = RuntimeError(
        "Cannot delete automation project without cascade while deliveries or queue records exist"
    )
    client = _client(fake_service)

    response = client.delete("/api/automation/projects/aut_1")

    assert response.status_code == 409
    assert "without cascade" in response.json()["detail"]
