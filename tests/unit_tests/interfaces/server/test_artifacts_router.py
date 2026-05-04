# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.agents.tasks.artifact_query_service import ArtifactQueryService
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import TaskArtifactEntry
from relay_teams.interfaces.server.deps import get_artifact_query_service
from relay_teams.interfaces.server.routers import artifacts_router


def _make_app(repo: TaskArtifactRepository) -> FastAPI:
    app = FastAPI()
    app.include_router(artifacts_router.router, prefix="/api")
    app.dependency_overrides[get_artifact_query_service] = lambda: ArtifactQueryService(
        repo
    )
    return app


def _seed_artifact(repo: TaskArtifactRepository, task_id: str) -> None:
    repo.ensure_artifact(task_id=task_id, spec_artifact_id="spec-1")
    for phase, event_type, description in (
        (TaskArtifactPhase.SPEC, "task_started", "Task started"),
        (TaskArtifactPhase.EXECUTION, "llm_execution_start", "LLM start"),
        (TaskArtifactPhase.VERIFICATION, "guardrail_report_completed", "Verify"),
        (TaskArtifactPhase.DELIVERY, "task_completed", "Done"),
    ):
        repo.append_entry(
            task_id=task_id,
            entry=TaskArtifactEntry(
                entry_id=f"e-{phase.value}",
                phase=phase,
                timestamp="2024-01-01T00:00:00+00:00",
                event_type=event_type,
                description=description,
            ),
        )
    repo.update_summary(task_id=task_id, summary="All done")


class TestGetTaskArtifact:
    def test_returns_artifact(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        _seed_artifact(repo, "task-1")
        client = TestClient(_make_app(repo))

        resp = client.get("/api/runs/run-1/tasks/task-1/artifact")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-1"
        assert len(data["entries"]) == 4

    def test_missing_returns_404(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        client = TestClient(_make_app(repo))

        resp = client.get("/api/runs/run-1/tasks/no-task/artifact")
        assert resp.status_code == 404


class TestGetTaskArtifactEntries:
    def test_returns_entries(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        _seed_artifact(repo, "task-1")
        client = TestClient(_make_app(repo))

        resp = client.get("/api/runs/run-1/tasks/task-1/artifact/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-1"
        assert data["total"] == 4
        assert len(data["items"]) == 4

    def test_filter_by_phase(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        _seed_artifact(repo, "task-1")
        client = TestClient(_make_app(repo))

        resp = client.get(
            "/api/runs/run-1/tasks/task-1/artifact/entries",
            params={"phase": "execution"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["phase"] == "execution"

    def test_filter_by_event_type(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        _seed_artifact(repo, "task-1")
        client = TestClient(_make_app(repo))

        resp = client.get(
            "/api/runs/run-1/tasks/task-1/artifact/entries",
            params={"event_type": "task_completed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_pagination(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        _seed_artifact(repo, "task-1")
        client = TestClient(_make_app(repo))

        resp = client.get(
            "/api/runs/run-1/tasks/task-1/artifact/entries",
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 4
        assert data["next_offset"] == 2

    def test_empty_artifact(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        repo.ensure_artifact(task_id="empty-task", spec_artifact_id="")
        client = TestClient(_make_app(repo))

        resp = client.get("/api/runs/run-1/tasks/empty-task/artifact/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestGetTaskArtifactSummary:
    def test_returns_summary(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        _seed_artifact(repo, "task-1")
        client = TestClient(_make_app(repo))

        resp = client.get("/api/runs/run-1/tasks/task-1/artifact/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-1"
        assert data["has_summary"] is True

    def test_missing_returns_404(self, tmp_path: Path) -> None:
        repo = TaskArtifactRepository(tmp_path / "test.db")
        client = TestClient(_make_app(repo))

        resp = client.get("/api/runs/run-1/tasks/no-task/artifact/summary")
        assert resp.status_code == 404
