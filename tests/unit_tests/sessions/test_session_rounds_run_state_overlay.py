from __future__ import annotations

from pathlib import Path

from relay_teams.media import InlineMediaContentPart
from relay_teams.media import MediaModality
from relay_teams.media import TextContentPart
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import IntentInput
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


def _build_service(db_path: Path) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=RunEventHub(),
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
