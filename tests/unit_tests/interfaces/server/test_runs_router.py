# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_run_service
from agent_teams.interfaces.server.routers import runs
from agent_teams.sessions.runs.run_models import IntentInput


class _FakeRunService:
    def __init__(self) -> None:
        self.resumed_run_ids: list[str] = []
        self.started_run_ids: list[str] = []
        self.raise_on_tool_approval = False
        self.created_run_inputs: list[IntentInput] = []
        self.exec_sessions: dict[str, dict[str, object]] = {
            "exec-1": {
                "exec_session_id": "exec-1",
                "run_id": "run-1",
                "status": "running",
                "command": "sleep 30",
            }
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

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)

    def list_exec_sessions(self, run_id: str) -> tuple[dict[str, object], ...]:
        _ = run_id
        return tuple(self.exec_sessions.values())

    def get_exec_session(
        self,
        *,
        run_id: str,
        exec_session_id: str,
    ) -> dict[str, object]:
        _ = run_id
        if exec_session_id not in self.exec_sessions:
            raise KeyError(exec_session_id)
        return self.exec_sessions[exec_session_id]

    async def stop_exec_session(
        self,
        *,
        run_id: str,
        exec_session_id: str,
    ) -> dict[str, object]:
        _ = run_id
        exec_session = self.get_exec_session(
            run_id=run_id,
            exec_session_id=exec_session_id,
        )
        exec_session["status"] = "stopped"
        return exec_session


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


def test_resume_route_rejects_none_like_run_id() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post("/api/runs/None:resume")

    assert response.status_code == 422
    assert fake_service.resumed_run_ids == []


def test_list_exec_sessions_route_returns_items() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/exec-sessions")

    assert response.status_code == 200
    assert response.json() == {"items": [fake_service.exec_sessions["exec-1"]]}


def test_get_exec_session_route_returns_single_terminal() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/exec-sessions/exec-1")

    assert response.status_code == 200
    assert response.json() == {"exec_session": fake_service.exec_sessions["exec-1"]}


def test_stop_exec_session_route_returns_updated_terminal() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post("/api/runs/run-1/exec-sessions/exec-1:stop")

    assert response.status_code == 200
    assert response.json() == {
        "exec_session": {
            "exec_session_id": "exec-1",
            "run_id": "run-1",
            "status": "stopped",
            "command": "sleep 30",
        }
    }
