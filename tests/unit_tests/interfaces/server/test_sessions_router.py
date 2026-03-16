from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_session_service
from agent_teams.interfaces.server.routers import sessions
from agent_teams.providers import AgentTokenSummary, RunTokenUsage, SessionTokenUsage
from agent_teams.sessions.session_models import SessionRecord


class _FakeSessionService:
    def __init__(self) -> None:
        self.updated_calls: list[tuple[str, dict[str, str]]] = []
        self.reflection_refresh_calls: list[tuple[str, str]] = []
        self.raise_missing = False

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        if self.raise_missing:
            raise KeyError(session_id)
        self.updated_calls.append((session_id, metadata))

    def create_session(  # pragma: no cover
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        raise AssertionError("not used")

    def list_sessions(self) -> tuple[SessionRecord, ...]:  # pragma: no cover
        raise AssertionError("not used")

    def get_session(self, session_id: str) -> SessionRecord:  # pragma: no cover
        raise AssertionError(f"not used: {session_id}")

    def delete_session(self, session_id: str) -> None:  # pragma: no cover
        raise AssertionError(f"not used: {session_id}")

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        return SessionTokenUsage(
            session_id=session_id,
            total_input_tokens=120,
            total_cached_input_tokens=48,
            total_output_tokens=30,
            total_reasoning_output_tokens=9,
            total_tokens=150,
            total_requests=3,
            total_tool_calls=1,
            by_role={
                "coordinator": AgentTokenSummary(
                    instance_id="",
                    role_id="coordinator",
                    input_tokens=120,
                    cached_input_tokens=48,
                    output_tokens=30,
                    reasoning_output_tokens=9,
                    total_tokens=150,
                    requests=3,
                    tool_calls=1,
                )
            },
        )

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return RunTokenUsage(
            run_id=run_id,
            total_input_tokens=44,
            total_cached_input_tokens=12,
            total_output_tokens=10,
            total_reasoning_output_tokens=4,
            total_tokens=54,
            total_requests=2,
            total_tool_calls=0,
            by_agent=[
                AgentTokenSummary(
                    instance_id="inst-1",
                    role_id="coordinator",
                    input_tokens=44,
                    cached_input_tokens=12,
                    output_tokens=10,
                    reasoning_output_tokens=4,
                    total_tokens=54,
                    requests=2,
                    tool_calls=0,
                )
            ],
        )

    def get_agent_reflection(
        self, session_id: str, instance_id: str
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "role_id": "writer",
            "summary": "Use concise drafts.",
            "updated_at": "2026-03-13T00:01:30Z",
            "source": "stored",
        }

    async def refresh_subagent_reflection(
        self, session_id: str, instance_id: str
    ) -> dict[str, object]:
        self.reflection_refresh_calls.append((session_id, instance_id))
        return {
            "session_id": session_id,
            "instance_id": instance_id,
            "role_id": "writer",
            "summary": "Use concise drafts.",
            "updated_at": "2026-03-13T00:02:00Z",
            "source": "manual",
        }


def _create_client(fake_service: _FakeSessionService) -> TestClient:
    app = FastAPI()
    app.include_router(sessions.router, prefix="/api")
    app.dependency_overrides[get_session_service] = lambda: fake_service
    return TestClient(app)


def test_update_session_route_accepts_metadata_payload() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/session-1",
        json={"metadata": {"title": "Renamed Session"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.updated_calls == [("session-1", {"title": "Renamed Session"})]


def test_update_session_route_returns_not_found_for_missing_session() -> None:
    fake_service = _FakeSessionService()
    fake_service.raise_missing = True
    client = _create_client(fake_service)

    response = client.patch(
        "/api/sessions/missing-session",
        json={"metadata": {"title": "Renamed Session"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


def test_get_session_token_usage_route_returns_extended_totals() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/token-usage")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "total_input_tokens": 120,
        "total_cached_input_tokens": 48,
        "total_output_tokens": 30,
        "total_reasoning_output_tokens": 9,
        "total_tokens": 150,
        "total_requests": 3,
        "total_tool_calls": 1,
        "by_role": {
            "coordinator": {
                "role_id": "coordinator",
                "input_tokens": 120,
                "cached_input_tokens": 48,
                "output_tokens": 30,
                "reasoning_output_tokens": 9,
                "total_tokens": 150,
                "requests": 3,
                "tool_calls": 1,
            }
        },
    }


def test_get_run_token_usage_route_returns_extended_totals() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/runs/run-1/token-usage")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-1",
        "total_input_tokens": 44,
        "total_cached_input_tokens": 12,
        "total_output_tokens": 10,
        "total_reasoning_output_tokens": 4,
        "total_tokens": 54,
        "total_requests": 2,
        "total_tool_calls": 0,
        "by_agent": [
            {
                "instance_id": "inst-1",
                "role_id": "coordinator",
                "input_tokens": 44,
                "cached_input_tokens": 12,
                "output_tokens": 10,
                "reasoning_output_tokens": 4,
                "total_tokens": 54,
                "requests": 2,
                "tool_calls": 0,
            }
        ],
    }


def test_get_agent_reflection_route_returns_projection() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.get("/api/sessions/session-1/agents/inst-1/reflection")

    assert response.status_code == 200
    assert response.json()["instance_id"] == "inst-1"
    assert response.json()["source"] == "stored"


def test_refresh_agent_reflection_route_returns_projection() -> None:
    fake_service = _FakeSessionService()
    client = _create_client(fake_service)

    response = client.post("/api/sessions/session-1/agents/inst-1/reflection:refresh")

    assert response.status_code == 200
    assert response.json()["source"] == "manual"
    assert fake_service.reflection_refresh_calls == [("session-1", "inst-1")]
