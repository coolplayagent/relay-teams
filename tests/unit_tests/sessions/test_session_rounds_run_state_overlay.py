from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.media import InlineMediaContentPart
from relay_teams.media import MediaModality
from relay_teams.media import TextContentPart
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions import session_service as session_service_module
from relay_teams.sessions.runs.todo_models import TodoItem
from relay_teams.sessions.runs.todo_models import TodoStatus
from relay_teams.sessions.runs.todo_repository import TodoRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.user_question_models import UserQuestionOption
from relay_teams.sessions.runs.user_question_models import UserQuestionPrompt
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


def _build_service(
    db_path: Path,
    todo_service: TodoService | None = None,
    user_question_repo: UserQuestionRepository | None = None,
) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        user_question_repo=user_question_repo,
        run_runtime_repo=RunRuntimeRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=RunEventHub(),
        todo_service=todo_service,
        run_intent_repo=RunIntentRepository(db_path),
    )


def test_session_rounds_include_persisted_run_state_overlay(tmp_path: Path) -> None:
    db_path = tmp_path / "round_state_overlay.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="do work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_runtime_repo = RunRuntimeRepository(db_path)
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    page = service.get_session_rounds("session-1", limit=8)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("intent_parts") == [{"kind": "text", "text": "do work"}]
    assert first.get("run_status") == "running"
    assert first.get("run_phase") == "running"
    assert first.get("is_recoverable") is True


def test_session_rounds_prefer_persisted_run_intent_parts(tmp_path: Path) -> None:
    db_path = tmp_path / "round_state_overlay_intent_parts.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="fallback objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_intent_repo = service._run_intent_repo
    assert run_intent_repo is not None
    run_intent_repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=(
                TextContentPart(text="这个是什么图片"),
                InlineMediaContentPart(
                    modality=MediaModality.IMAGE,
                    mime_type="image/png",
                    base64_data="QUJD",
                    name="image.png",
                ),
            ),
        ),
    )

    page = service.get_session_rounds("session-1", limit=8)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("intent") == "这个是什么图片\n\n[image: image.png]"
    assert first.get("intent_parts") == [
        {"kind": "text", "text": "这个是什么图片"},
        {
            "kind": "inline_media",
            "modality": "image",
            "mime_type": "image/png",
            "base64_data": "QUJD",
            "name": "image.png",
            "size_bytes": None,
            "width": None,
            "height": None,
            "duration_ms": None,
            "thumbnail_asset_id": None,
        },
    ]


def test_session_rounds_prefer_display_input_parts(tmp_path: Path) -> None:
    db_path = tmp_path / "round_state_overlay_display_input.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="expanded skill prompt",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_intent_repo = service._run_intent_repo
    assert run_intent_repo is not None
    run_intent_repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("Use the time skill.\n\n现在几点了"),
            display_input=content_parts_from_text("/time 现在几点了"),
            skills=("time",),
        ),
    )

    page = service.get_session_rounds("session-1", limit=8)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("intent") == "/time 现在几点了"
    assert first.get("intent_parts") == [{"kind": "text", "text": "/time 现在几点了"}]


def test_session_rounds_timeline_bypasses_full_round_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "timeline_round_state_overlay.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="timeline only",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    def fail_full_round_projection(**_kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("timeline requests should not build full round payloads")

    monkeypatch.setattr(
        session_service_module,
        "build_session_rounds",
        fail_full_round_projection,
    )

    page = service.get_session_rounds("session-1", timeline=True)
    items = page.get("items")

    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("run_id") == "run-1"
    assert first.get("intent") == "timeline only"
    assert "coordinator_messages" not in first


def test_session_rounds_timeline_applies_runtime_phase_and_todo_overlay(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "timeline_runtime_todo_overlay.db"
    todo_service = TodoService(repository=TodoRepository(db_path))
    service = _build_service(db_path, todo_service=todo_service)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="timeline runtime",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_runtime_repo = RunRuntimeRepository(db_path)
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.STOPPING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    todo_service.replace_for_run(
        run_id="run-1",
        session_id="session-1",
        items=(TodoItem(content="Check timeline", status=TodoStatus.COMPLETED),),
    )

    page = service.get_session_rounds("session-1", timeline=True)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first["run_status"] == "stopping"
    assert first["run_phase"] == "stopping"
    assert first["is_recoverable"] is False
    todo = first.get("todo")
    assert isinstance(todo, dict)
    assert todo["run_id"] == "run-1"
    todo_items = todo.get("items")
    assert isinstance(todo_items, list)
    assert todo_items[0]["content"] == "Check timeline"


def test_session_rounds_timeline_batches_pending_user_question_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "timeline_user_question_batch.db"
    user_question_repo = UserQuestionRepository(db_path)
    service = _build_service(db_path, user_question_repo=user_question_repo)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="ask user",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_runtime_repo = RunRuntimeRepository(db_path)
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    user_question_repo.upsert_requested(
        question_id="question-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Continue?",
                options=(UserQuestionOption(label="Yes"),),
            ),
        ),
    )

    def fail_list_by_run(run_id: str) -> object:
        raise AssertionError(f"per-run question lookup should not run: {run_id}")

    monkeypatch.setattr(user_question_repo, "list_by_run", fail_list_by_run)

    page = service.get_session_rounds("session-1", timeline=True)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first["run_phase"] == "awaiting_manual_action"


def test_session_rounds_timeline_batches_run_intent_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "timeline_run_intent_batch.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="fallback objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_intent_repo = service._run_intent_repo
    assert run_intent_repo is not None
    run_intent_repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("batched timeline intent"),
        ),
    )

    def fail_get(run_id: str, *, fallback_session_id: str | None = None) -> object:
        _ = fallback_session_id
        raise AssertionError(f"per-run intent lookup should not run: {run_id}")

    monkeypatch.setattr(run_intent_repo, "get", fail_get)

    page = service.get_session_rounds("session-1", timeline=True)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("intent") == "batched timeline intent"


def test_session_rounds_timeline_batches_runtime_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "timeline_runtime_batch.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="runtime projection",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_runtime_repo = service._run_runtime_repo
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    def fail_get(run_id: str) -> object:
        raise AssertionError(f"per-run runtime lookup should not run: {run_id}")

    monkeypatch.setattr(run_runtime_repo, "get", fail_get)

    page = service.get_session_rounds("session-1", timeline=True)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("run_status") == "running"
    assert first.get("run_phase") == "running"
