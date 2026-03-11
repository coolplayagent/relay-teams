# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_reflection_service
from agent_teams.interfaces.server.routers import reflection
from agent_teams.reflection.models import (
    DailyMemoryKind,
    ReflectionJobRecord,
    ReflectionJobStatus,
    ReflectionJobType,
)


class _FakeMemoryView:
    def __init__(self, content: str) -> None:
        self.path = "memory.md"
        self.exists = True
        self.content = content


class _FakeReflectionService:
    def list_jobs(self, *, limit: int = 50) -> tuple[ReflectionJobRecord, ...]:
        _ = limit
        return (
            ReflectionJobRecord.model_validate(
                {
                    "job_id": "rjob-1",
                    "job_type": ReflectionJobType.DAILY_REFLECTION,
                    "session_id": "session-1",
                    "run_id": "run-1",
                    "task_id": "task-1",
                    "instance_id": "inst-1",
                    "role_id": "writer_agent",
                    "workspace_id": "workspace-1",
                    "conversation_id": "conversation-1",
                    "memory_owner_scope": "session_role",
                    "memory_owner_id": "session-1:writer_agent",
                    "trigger_date": "2026-03-11",
                    "status": ReflectionJobStatus.QUEUED,
                    "attempt_count": 0,
                    "last_error": None,
                    "created_at": "2026-03-11T00:00:00+00:00",
                    "updated_at": "2026-03-11T00:00:00+00:00",
                }
            ),
        )

    def retry_job(self, job_id: str) -> ReflectionJobRecord:
        return self.list_jobs()[0].model_copy(update={"job_id": job_id})

    def read_long_term_memory(
        self, *, session_id: str, role_id: str
    ) -> _FakeMemoryView:
        _ = (session_id, role_id)
        return _FakeMemoryView("# MEMORY")

    def read_daily_memory(
        self,
        *,
        instance_id: str,
        memory_date: str,
        kind: DailyMemoryKind,
    ) -> _FakeMemoryView:
        _ = (instance_id, memory_date, kind)
        return _FakeMemoryView("# Daily Digest")


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(reflection.router, prefix="/api")
    app.dependency_overrides[get_reflection_service] = lambda: _FakeReflectionService()
    return TestClient(app)


def test_reflection_router_lists_jobs() -> None:
    client = _create_client()

    response = client.get("/api/reflection/jobs")

    assert response.status_code == 200
    assert response.json()[0]["job_id"] == "rjob-1"


def test_reflection_router_reads_memory_files() -> None:
    client = _create_client()

    long_term = client.get(
        "/api/reflection/memory/session-roles/session-1/writer_agent"
    )
    daily = client.get(
        "/api/reflection/memory/instances/inst-1/daily/2026-03-11?kind=digest"
    )

    assert long_term.status_code == 200
    assert long_term.json()["content"] == "# MEMORY"
    assert daily.status_code == 200
    assert daily.json()["content"] == "# Daily Digest"
