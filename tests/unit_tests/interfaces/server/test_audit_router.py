from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.audit import (
    AuditEventCreate,
    AuditEventFilter,
    AuditEventPage,
    AuditEventRecord,
    AuditEventType,
)
from relay_teams.interfaces.server.deps import get_audit_service
from relay_teams.interfaces.server.routers import audit


class _FakeAuditService:
    def __init__(self) -> None:
        self.queries: list[AuditEventFilter] = []

    async def list_events_async(self, query: AuditEventFilter) -> AuditEventPage:
        self.queries.append(query)
        event = AuditEventRecord(
            id=7,
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
            **AuditEventCreate(
                event_type=AuditEventType.SHELL_COMMAND,
                trace_id="trace-1",
                run_id="run-1",
                session_id="session-1",
                task_id="task-1",
                instance_id="instance-1",
                role_id="coder",
                tool_call_id="toolcall-1",
                action="execute_shell_command",
                target="uv run pytest",
                command="uv run pytest",
                outcome="completed",
                metadata={"exit_code": 0},
                occurred_at=datetime(2026, 5, 1, tzinfo=UTC),
            ).model_dump(),
        )
        return AuditEventPage(items=(event,), next_after_id=None)


def test_audit_route_returns_filtered_events() -> None:
    service = _FakeAuditService()
    app = FastAPI()
    app.include_router(audit.router, prefix="/api")
    app.dependency_overrides[get_audit_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/audit?event_type=shell_command&run_id=run-1&after_id=2&limit=50"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["event_type"] == "shell_command"
    assert payload["items"][0]["command"] == "uv run pytest"
    assert payload["next_after_id"] is None
    assert service.queries == [
        AuditEventFilter(
            event_type=AuditEventType.SHELL_COMMAND,
            run_id="run-1",
            after_id=2,
            limit=50,
        )
    ]


def test_audit_route_validates_limit() -> None:
    service = _FakeAuditService()
    app = FastAPI()
    app.include_router(audit.router, prefix="/api")
    app.dependency_overrides[get_audit_service] = lambda: service
    client = TestClient(app)

    response = client.get("/api/audit?limit=501")

    assert response.status_code == 422
