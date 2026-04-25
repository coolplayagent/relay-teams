# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.deps import get_run_service, get_skill_registry
from relay_teams.interfaces.server.routers import runs
from relay_teams.media import (
    ContentPart,
    InlineMediaContentPart,
    MediaModality,
    MediaRefContentPart,
    content_parts_from_text,
)
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.user_question_models import UserQuestionAnswer


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
        self.answered_user_questions: list[
            tuple[str, str, tuple[UserQuestionAnswer, ...]]
        ] = []

    def create_run(self, intent_input) -> tuple[str, str]:
        self.created_run_inputs.append(intent_input)
        return ("run-1", "session-1")

    async def create_run_async(self, intent_input) -> tuple[str, str]:
        return self.create_run(intent_input)

    def resume_run(self, run_id: str) -> str:
        self.resumed_run_ids.append(run_id)
        return "session-1"

    async def resume_run_async(self, run_id: str) -> str:
        return self.resume_run(run_id)

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

    async def resolve_tool_approval_async(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = "",
    ) -> None:
        self.resolve_tool_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            action=action,
            feedback=feedback,
        )

    def ensure_run_started(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)

    async def ensure_run_started_async(self, run_id: str) -> None:
        self.ensure_run_started(run_id)

    def inject_message(self, *, run_id: str, source, content: str):
        if self.raise_on_inject:
            raise ValueError("Injection content must not be empty")
        self.inject_calls.append((run_id, source.value, content))
        return type(
            "_InjectedRecord",
            (),
            {"model_dump": lambda self: {"run_id": run_id, "content": content}},
        )()

    async def inject_message_async(self, *, run_id: str, source, content: str):
        return self.inject_message(run_id=run_id, source=source, content=content)

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

    async def inject_subagent_message_async(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        self.inject_subagent_message(
            run_id=run_id,
            instance_id=instance_id,
            content=content,
        )

    def list_background_tasks(self, run_id: str) -> tuple[dict[str, object], ...]:
        _ = run_id
        return tuple(self.background_tasks.values())

    async def list_background_tasks_async(
        self,
        run_id: str,
    ) -> tuple[dict[str, object], ...]:
        return self.list_background_tasks(run_id)

    def get_todo(self, run_id: str) -> dict[str, object]:
        _ = run_id
        return dict(self.todo)

    async def get_todo_async(self, run_id: str) -> dict[str, object]:
        return self.get_todo(run_id)

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

    async def get_background_task_async(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        return self.get_background_task(
            run_id=run_id,
            background_task_id=background_task_id,
        )

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

    async def list_monitors_async(
        self,
        run_id: str,
    ) -> tuple[dict[str, object], ...]:
        return self.list_monitors(run_id)

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

    async def create_monitor_async(
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
        return self.create_monitor(
            run_id=run_id,
            source_kind=source_kind,
            source_key=source_key,
            rule=rule,
            action_type=action_type,
            created_by_instance_id=created_by_instance_id,
            created_by_role_id=created_by_role_id,
            tool_call_id=tool_call_id,
        )

    def stop_monitor(self, *, run_id: str, monitor_id: str) -> dict[str, object]:
        _ = run_id
        monitor = self.monitors[monitor_id]
        monitor["status"] = "stopped"
        return monitor

    async def stop_monitor_async(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> dict[str, object]:
        return self.stop_monitor(run_id=run_id, monitor_id=monitor_id)

    def answer_user_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers,
    ) -> dict[str, object]:
        self.answered_user_questions.append((run_id, question_id, answers.answers))
        return {
            "status": "ok",
            "run_id": run_id,
            "question_id": question_id,
            "answer_count": len(answers.answers),
        }

    async def answer_user_question_async(
        self,
        *,
        run_id: str,
        question_id: str,
        answers,
    ) -> dict[str, object]:
        return self.answer_user_question(
            run_id=run_id,
            question_id=question_id,
            answers=answers,
        )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        _ = run_id
        return []

    async def list_open_tool_approvals_async(
        self,
        run_id: str,
    ) -> list[dict[str, str]]:
        return self.list_open_tool_approvals(run_id)

    def list_user_questions(self, run_id: str) -> list[dict[str, object]]:
        _ = run_id
        return []

    async def list_user_questions_async(
        self,
        run_id: str,
    ) -> list[dict[str, object]]:
        return self.list_user_questions(run_id)

    async def stop_run_async(self, run_id: str) -> None:
        _ = run_id

    async def stop_subagent_async(
        self,
        run_id: str,
        instance_id: str,
    ) -> dict[str, str]:
        return {"instance_id": instance_id, "run_id": run_id}


class _CancellationAwareRunService(_FakeRunService):
    def __init__(self) -> None:
        super().__init__()
        self.create_entered = asyncio.Event()
        self.release_create = asyncio.Event()
        self.run_started = asyncio.Event()

    async def create_run_async(self, intent_input) -> tuple[str, str]:
        self.created_run_inputs.append(intent_input)
        self.create_entered.set()
        await self.release_create.wait()
        return ("run-1", "session-1")

    async def ensure_run_started_async(self, run_id: str) -> None:
        self.started_run_ids.append(run_id)
        self.run_started.set()


class _FakeSkillRegistry:
    def __init__(self) -> None:
        self.resolve_calls: list[tuple[tuple[str, ...], bool, str | None]] = []

    def resolve_known(
        self,
        skill_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
        expand_wildcards: bool = True,
    ) -> tuple[str, ...]:
        _ = expand_wildcards
        self.resolve_calls.append((skill_names, strict, consumer))
        if "missing" in skill_names:
            raise ValueError("Unknown skills: ['missing']")
        return tuple(skill_names)


class _FakeSessionRecord:
    def __init__(self) -> None:
        self.workspace_id = "workspace-1"


class _FakeSessionService:
    def get_session(self, session_id: str) -> _FakeSessionRecord:
        _ = session_id
        return _FakeSessionRecord()


class _FakeMediaAssetService:
    def __init__(self) -> None:
        self.normalize_calls: list[tuple[ContentPart, ...]] = []

    def normalize_content_parts(
        self,
        *,
        session_id: str,
        workspace_id: str,
        parts: tuple[ContentPart, ...],
    ) -> tuple[ContentPart, ...]:
        _ = session_id, workspace_id
        self.normalize_calls.append(parts)
        normalized: list[ContentPart] = []
        for index, part in enumerate(parts):
            if isinstance(part, InlineMediaContentPart):
                normalized.append(
                    MediaRefContentPart(
                        asset_id=f"asset-{len(self.normalize_calls)}-{index}",
                        session_id="session-1",
                        modality=part.modality,
                        mime_type=part.mime_type,
                        name=part.name,
                        url=f"/api/sessions/session-1/media/asset-{index}",
                        size_bytes=part.size_bytes,
                    )
                )
                continue
            normalized.append(part)
        return tuple(normalized)


class _FakeContainer:
    def __init__(self, media_asset_service: _FakeMediaAssetService) -> None:
        self.session_service = _FakeSessionService()
        self.media_asset_service = media_asset_service


def _create_client(
    fake_service: _FakeRunService,
    fake_skill_registry: _FakeSkillRegistry | None = None,
    fake_container: _FakeContainer | None = None,
) -> TestClient:
    app = FastAPI()
    registry = fake_skill_registry or _FakeSkillRegistry()
    if fake_container is not None:
        app.state.container = fake_container
    app.include_router(runs.router, prefix="/api")
    app.dependency_overrides[get_run_service] = lambda: fake_service
    app.dependency_overrides[get_skill_registry] = lambda: registry
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


@pytest.mark.asyncio
async def test_create_and_start_run_finishes_startup_after_cancellation() -> None:
    fake_service = _CancellationAwareRunService()
    intent_input = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("hello"),
    )

    task = asyncio.create_task(
        runs._create_and_start_run(cast(SessionRunService, fake_service), intent_input)
    )
    await fake_service.create_entered.wait()
    task.cancel()
    fake_service.release_create.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(fake_service.run_started.wait(), timeout=1)

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


def test_create_run_route_accepts_explicit_skills() -> None:
    fake_service = _FakeRunService()
    fake_skill_registry = _FakeSkillRegistry()
    client = _create_client(fake_service, fake_skill_registry)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "summarize"}],
            "execution_mode": "ai",
            "skills": ["pdf"],
        },
    )

    assert response.status_code == 200
    created = fake_service.created_run_inputs[0]
    assert created.intent == "summarize"
    assert created.skills == ("pdf",)
    assert fake_skill_registry.resolve_calls == [
        (("pdf",), True, "interfaces.server.routers.runs.create_run")
    ]


def test_create_run_route_reuses_input_media_refs_for_display_input() -> None:
    fake_service = _FakeRunService()
    fake_media_service = _FakeMediaAssetService()
    client = _create_client(
        fake_service,
        fake_container=_FakeContainer(fake_media_service),
    )
    inline_media = {
        "kind": "inline_media",
        "modality": MediaModality.IMAGE.value,
        "mime_type": "image/png",
        "base64_data": "aGVsbG8=",
        "name": "diagram.png",
        "size_bytes": 5,
    }

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "analyze"}, inline_media],
            "display_input": [
                {"kind": "text", "text": "/vision analyze"},
                inline_media,
            ],
            "execution_mode": "ai",
        },
    )

    assert response.status_code == 200
    assert len(fake_media_service.normalize_calls) == 1
    created = fake_service.created_run_inputs[0]
    input_media = created.input[1]
    display_media = created.display_input[1]
    assert isinstance(input_media, MediaRefContentPart)
    assert isinstance(display_media, MediaRefContentPart)
    assert input_media.asset_id == display_media.asset_id
    assert created.display_intent == "/vision analyze\n\n[image: diagram.png]"


def test_create_run_route_rejects_unknown_explicit_skill() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs",
        json={
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "summarize"}],
            "execution_mode": "ai",
            "skills": ["missing"],
        },
    )

    assert response.status_code == 400
    assert fake_service.created_run_inputs == []


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


def test_answer_user_question_route_awaits_service_call() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.post(
        "/api/runs/run-1/questions/question-1:answer",
        json={"answers": [{"selections": [{"label": "A"}]}]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "run_id": "run-1",
        "question_id": "question-1",
        "answer_count": 1,
    }
    answered = fake_service.answered_user_questions
    assert len(answered) == 1
    assert answered[0][0] == "run-1"
    assert answered[0][1] == "question-1"
    assert len(answered[0][2]) == 1
    assert answered[0][2][0].selections[0].label == "A"


def test_list_monitors_route_awaits_service_call() -> None:
    fake_service = _FakeRunService()
    client = _create_client(fake_service)

    response = client.get("/api/runs/run-1/monitors")

    assert response.status_code == 200
    assert response.json() == {"items": [fake_service.monitors["mon-1"]]}
