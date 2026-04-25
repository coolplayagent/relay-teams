# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.agents.orchestration.task_contracts import TaskDraft, TaskUpdate
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from relay_teams.interfaces.server.deps import get_task_service
from relay_teams.interfaces.server.routers import tasks


class _FakeTaskService:
    def __init__(self) -> None:
        self.task = TaskRecord(
            envelope=TaskEnvelope(
                task_id="task_1",
                session_id="session_1",
                parent_task_id="root_1",
                trace_id="run_1",
                role_id="writer",
                title="Write summary",
                objective="Summarize the latest status.",
                verification=VerificationPlan(checklist=("non_empty_response",)),
            ),
            status=TaskStatus.CREATED,
        )
        self.created_payload: tuple[str, list[TaskDraft]] | None = None

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        return (self.task,)

    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, object]:
        self.created_payload = (run_id, tasks)
        return {"created_count": len(tasks), "tasks": [{"task_id": "task_2"}]}

    def list_delegated_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, object]:
        return {
            "tasks": [
                {
                    "task_id": self.task.envelope.task_id,
                    "run_id": run_id,
                    "include_root": include_root,
                }
            ]
        }

    def get_task(self, *, task_id: str) -> TaskRecord:
        if task_id != self.task.envelope.task_id:
            raise KeyError(task_id)
        return self.task

    def update_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> dict[str, object]:
        if task_id != self.task.envelope.task_id:
            raise KeyError(task_id)
        return {
            "task": {
                "task_id": task_id,
                "run_id": run_id,
                "title": update.title or self.task.envelope.title,
                "objective": update.objective or self.task.envelope.objective,
            }
        }


def _create_test_client(service: _FakeTaskService | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    resolved_service = service or _FakeTaskService()
    app.dependency_overrides[get_task_service] = lambda: resolved_service
    return TestClient(app)


def test_task_routes_call_service() -> None:
    client = _create_test_client()

    responses = [
        client.get("/api/tasks"),
        client.get("/api/tasks/runs/run_1", params={"include_root": "true"}),
        client.get("/api/tasks/task_1"),
        client.patch(
            "/api/tasks/task_1",
            json={
                "title": "Updated title",
                "objective": "Updated objective",
            },
        ),
    ]

    assert [response.status_code for response in responses] == [200] * len(responses)


def test_create_tasks_for_run_uses_async_service_directly() -> None:
    service = _FakeTaskService()
    client = _create_test_client(service)

    response = client.post(
        "/api/tasks/runs/run_1",
        json={
            "tasks": [
                {
                    "objective": "Summarize the latest status.",
                    "title": "Write summary",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    assert service.created_payload is not None
    assert service.created_payload[0] == "run_1"
