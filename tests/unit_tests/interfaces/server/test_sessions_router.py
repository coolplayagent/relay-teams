from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_session_service
from agent_teams.interfaces.server.routers import sessions
from agent_teams.sessions.session_models import SessionRecord


class _FakeSessionService:
    def __init__(self) -> None:
        self.updated_calls: list[tuple[str, dict[str, str]]] = []
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
