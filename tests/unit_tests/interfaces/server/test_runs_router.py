# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.deps import get_run_service
from relay_teams.interfaces.server.routers import runs
from relay_teams.sessions.runs.run_models import IntentInput


class _FakeRunService:
    def __init__(self) -> None:
        self.resumed_run_ids: list[str] = []
        self.started_run_ids: list[str] = []
        self.resolved_tool_approvals: list[tuple[str, str, str, str]] = []
        self.raise_on_tool_approval = False
        self.inject_calls: list[tuple[str, str, str]] = []
        self.subagent_inject_calls: list[tuple[str, str, str]] = []
        self.raise_on_inject = False
        self.raise_on_subagent_inject = False
        self.created_run_inputs: list[IntentInput] = []
        self.background_tasks: dict[str, dict[str, object]] = {
            "exec-1": {
                "background_task_id": "exec-1",
                "run_id": "run-1",
                "status": "running",
                "command": "sleep 30",
            }
        }
        self.monitors: dict[str, dict[str, object]] = {
            "mon-1": {
                "monitor_id": "mon-1",
                "run_id": "run-1",
                "session_id": "session-1",
                "source_kind": "background_task",
                "source_key": "exec-1",
                "status": "active",
            }
        }
        self.todo = {
            "run_id": "run-1",
            "session_id": "session-1",
            "items": [
                {"content": "Inspect issue", "status": "completed"},
                {"content": "Implement todo flow", "status": "in_progress"},
            ],
            "version": 2,
            "updated_at": "2026-04-20T00:00:00+00:00",
            "updated_by_role_id": "MainAgent",
            "updated_by_instance_id": "inst-1",
        }

    def create_run(self, intent_input) -> tuple[str, str]:
        self.created_run_inputs.append(intent_input)
        return ("run-1", "session-1")

    def resume_run(self, run_id: str) -> str:
        self.resumed_run_ids.append(run_id)
        return "session-1"

    def resolve_tool_approval(
        self,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = "",
    ) -> None:
        if self.raise_on_tool_approval:
            raise RuntimeError(
                "Run run-1 is stopped. Resume the run before resolving tool approval."
            )
        self.resolved_tool_approvals.append((run_id, tool_call_id, action, feedback))

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)

    def inject_message(self, *, run_id: str, source, content: str):
        if self.raise_on_inject:
            raise ValueError("Injection content must not be empty")
        self.inject_calls.append((run_id, source.value, content))
        return type(
            "_InjectedRecord",
            (),
            {"model_dump": lambda self: {"run_id": run_id, "content": content}},
        )()

    def inject_subagent_message(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        if self.raise_on_subagent_inject:
            raise ValueError("Injection content must not be empty")
        self.subagent_inject_calls.append((run_id, instance_id, content))

    def list_background_tasks(self, run_id: str) -> tuple[dict[str, object], ...]:
        _ = run_id
        return tuple(self.background_tasks.values())

    def get_todo(self, run_id: str) -> dict[str, object]:
        _ = run_id
        return dict(self.todo)

    def get_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        _ = run_id
        if background_task_id not in self.background_tasks:
            raise KeyError(background_task_id)
        return self.background_tasks[background_task_id]

    async def stop_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        _ = run_id
        background_task = self.get_background_task(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        background_task["status"] = "stopped"
        return background_task

    def list_monitors(self, run_id: str) -> tuple[dict[str, object], ...]:
        _ = run_id
        return tuple(self.monitors.values())

    def create_monitor(
        self,
        *,
        run_id: str,
        source_kind,
        source_key: str,
        rule,
        action_type,
        created_by_instance_id: str | None = None,
        created_by_role_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, object]:
        _ = (
            run_id,
            source_kind,
            rule,
            action_type,
            created_by_instance_id,
            created_by_role_id,
            tool_call_id,
        )
        monitor: dict[str, object] = {
            "monitor_id": "mon-2",
            "run_id": "run-1",
            "session_id": "session-1",
            "source_kind": "background_task",
            "source_key": source_key,
            "status": "active",
        }
        self.monitors["mon-2"] = monitor
        return monitor

    def stop_monitor(self, *, run_id: str, monitor_id: str) -> dict[str, object]:
        _ = run_id
        monitor = self.monitors[monitor_id]
        monitor["status"] = "stopped"
        return monitor


def _create_client(fake_service: _FakeRunService) -> TestClient:
    app = FastAPI()
    app.include_router(runs.router, prefix="/api")
    app.dependency_overrides[get_run_service] = lambda: fake_service
    return TestClient(app)


def test_resume_route_marks_run_for_resume_and_starts_worker() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post("/api/runs/run-1:resume")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "run_id": "run-1",
        "session_id": "session-1",
    }
    assert fake_service.resumed_run_ids == ["run-1"]
    assert fake_service.started_run_ids == ["run-1"]


def test_create_run_route_accepts_yolo() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
            "yolo": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "session_id": "session-1"}
    created = fake_service.created_run_inputs[0]
    assert created.intent == "hello"
    assert created.yolo is True
    assert fake_service.started_run_ids == ["run-1"]


def test_create_run_route_rejects_none_like_session_id() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "None",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
        },
    )

    assert response.status_code == 422
    assert fake_service.created_run_inputs == []


def test_create_run_route_accepts_thinking_config() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
            "yolo": False,
            "thinking": {"enabled": True, "effort": "high"},
        },
    )

    assert response.status_code == 200
    created = fake_service.created_run_inputs[0]
    assert created.thinking.enabled is True
    assert created.thinking.effort == "high"
    assert fake_service.started_run_ids == ["run-1"]


def test_create_run_route_accepts_target_role_id() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
            "target_role_id": "writer",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-1",
        "session_id": "session-1",
        "target_role_id": "writer",
    }
    created = fake_service.created_run_inputs[0]
    assert created.intent == "hello"
    assert created.target_role_id == "writer"
    assert fake_service.started_run_ids == ["run-1"]


def test_inject_message_route_rejects_whitespace_only_content() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/inject",
        json={"source": "user", "content": "   "},
    )

    assert response.status_code == 422
    assert fake_service.inject_calls == []


def test_inject_message_route_maps_service_validation_errors_to_bad_request() -> None:
    fake_service = _FakeRunService()
    fake_service.raise_on_inject = True
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/inject",
        json={"source": "user", "content": "hello"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Injection content must not be empty"


def test_inject_subagent_route_rejects_whitespace_only_content() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/subagents/inst-1/inject",
        json={"content": "\t"},
    )

    assert response.status_code == 422
    assert fake_service.subagent_inject_calls == []


def test_inject_subagent_route_maps_service_validation_errors_to_bad_request() -> None:
    fake_service = _FakeRunService()
    fake_service.raise_on_subagent_inject = True
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/subagents/inst-1/inject",
        json={"content": "continue"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Injection content must not be empty"


def test_create_run_route_rejects_legacy_intent_field() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "intent": "hello",
            "execution_mode": "ai",
        },
    )

    assert response.status_code == 422
    assert fake_service.created_run_inputs == []


def test_resolve_tool_approval_route_returns_conflict_for_stopped_run() -> None:
    fake_service = _FakeRunService()
    fake_service.raise_on_tool_approval = True
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/tool-approvals/call-1/resolve",
        json={"action": "approve", "feedback": ""},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Run run-1 is stopped. Resume the run before resolving tool approval."
    )


def test_resolve_tool_approval_route_accepts_approve_exact() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/tool-approvals/call-1/resolve",
        json={"action": "approve_exact", "feedback": "persist this"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "action": "approve_exact"}
    assert fake_service.resolved_tool_approvals == [
        ("run-1", "call-1", "approve_exact", "persist this")
    ]


def test_resume_route_rejects_none_like_run_id() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post("/api/runs/None:resume")

    assert response.status_code == 422
    assert fake_service.resumed_run_ids == []


def test_list_background_tasks_route_returns_items() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/background-tasks")

    assert response.status_code == 200
    assert response.json() == {"items": [fake_service.background_tasks["exec-1"]]}


def test_get_todo_route_returns_snapshot() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/todo")

    assert response.status_code == 200
    assert response.json() == {"todo": fake_service.todo}


def test_get_background_task_route_returns_single_terminal() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/background-tasks/exec-1")

    assert response.status_code == 200
    assert response.json() == {
        "background_task": fake_service.background_tasks["exec-1"]
    }


def test_stop_background_task_route_returns_updated_terminal() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post("/api/runs/run-1/background-tasks/exec-1:stop")

    assert response.status_code == 200
    assert response.json() == {
        "background_task": {
            "background_task_id": "exec-1",
            "run_id": "run-1",
            "status": "stopped",
            "command": "sleep 30",
        }
    }


def test_list_monitors_route_returns_items() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/monitors")

    assert response.status_code == 200
    assert response.json() == {"items": [fake_service.monitors["mon-1"]]}


def test_create_monitor_route_returns_monitor() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/monitors",
        json={
            "source_kind": "background_task",
            "source_key": "exec-1",
            "event_names": ["background_task.line"],
            "patterns": ["ERROR"],
            "action_type": "wake_instance",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"monitor": fake_service.monitors["mon-2"]}


def test_stop_monitor_route_returns_updated_monitor() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post("/api/runs/run-1/monitors/mon-1:stop")

    assert response.status_code == 200
    assert response.json() == {
        "monitor": {
            "monitor_id": "mon-1",
            "run_id": "run-1",
            "session_id": "session-1",
            "source_kind": "background_task",
            "source_key": "exec-1",
            "status": "stopped",
        }
    }
