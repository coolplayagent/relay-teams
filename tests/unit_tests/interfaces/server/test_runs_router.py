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
        self.ensure_called = False
        self.raise_on_tool_approval = False
        self.created_run_inputs: list[IntentInput] = []

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

    def ensure_run_started(self, run_id: str) -> None:  # pragma: no cover
        self.ensure_called = True
        raise AssertionError(f"resume route should not start worker for {run_id}")


def _create_client(fake_service: _FakeRunService) -> TestClient:
    app = FastAPI()
    app.include_router(runs.router, prefix="/api")
    app.dependency_overrides[get_run_service] = lambda: fake_service
    return TestClient(app)


def test_resume_route_marks_run_for_resume_without_starting_worker() -> None:
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
    assert fake_service.ensure_called is False


def test_create_run_route_accepts_yolo() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "intent": "hello",
            "execution_mode": "ai",
            "yolo": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "session_id": "session-1"}
    created = fake_service.created_run_inputs[0]
    assert created.yolo is True


def test_create_run_route_accepts_thinking_config() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "intent": "hello",
            "execution_mode": "ai",
            "yolo": False,
            "thinking": {"enabled": True, "effort": "high"},
        },
    )

    assert response.status_code == 200
    created = fake_service.created_run_inputs[0]
    assert created.thinking.enabled is True
    assert created.thinking.effort == "high"


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
