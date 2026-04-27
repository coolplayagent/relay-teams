from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.media import ContentPartsAdapter, content_parts_from_text
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.reminders import render_system_reminder
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.enums import ExecutionMode
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.todo_repository import TodoRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.session_metadata import (
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_AUTO,
    SESSION_TITLE_SOURCE_MANUAL,
)
from relay_teams.sessions.session_models import SessionMetadataPatch
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


def _build_service(
    db_path: Path,
    *,
    run_intent_repo: RunIntentRepository | None = None,
) -> SessionService:
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        todo_service=TodoService(repository=TodoRepository(db_path)),
        role_registry=role_registry,
        run_intent_repo=run_intent_repo,
    )


def test_get_session_returns_auto_title_from_first_run_intent_display_input(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_auto_title_intent.db"
    run_intent_repo = RunIntentRepository(db_path)
    service = _build_service(db_path, run_intent_repo=run_intent_repo)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    run_intent_repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("Internal expanded request"),
            display_input=content_parts_from_text("/review issue 524\nwith details"),
            execution_mode=ExecutionMode.AI,
        ),
    )

    session = service.get_session("session-1")

    assert session.metadata == {
        "title": "/review issue 524",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
    }
    assert SessionRepository(db_path).get("session-1").metadata == {}


def test_list_sessions_returns_auto_title_from_first_user_message(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_auto_title_message.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    message_repo = MessageRepository(db_path)
    _ = message_repo.append_user_prompt_if_missing(
        session_id="session-1",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="  First user request  \nsecond line",
        workspace_id="default",
    )

    sessions = service.list_sessions()

    assert sessions[0].metadata == {
        "title": "First user request",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
    }
    assert SessionRepository(db_path).get("session-1").metadata == {}


def test_list_sessions_cache_invalidates_after_session_update(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_cache_update.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    assert service.list_sessions()[0].metadata == {}

    service.update_session(
        "session-1",
        SessionMetadataPatch(title="Manual title"),
    )

    assert service.list_sessions()[0].metadata["title"] == "Manual title"


def test_list_sessions_auto_title_skips_empty_preloaded_run_intent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_auto_title_skips_empty_intent.db"
    run_intent_repo = RunIntentRepository(db_path)
    service = _build_service(db_path, run_intent_repo=run_intent_repo)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    run_intent_repo.upsert(
        run_id="run-empty",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text(""),
            execution_mode=ExecutionMode.AI,
        ),
    )
    time.sleep(0.001)
    run_intent_repo.upsert(
        run_id="run-title",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("Later useful request"),
            execution_mode=ExecutionMode.AI,
        ),
    )

    sessions = service.list_sessions()

    assert sessions[0].metadata == {
        "title": "Later useful request",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
    }


def test_list_sessions_auto_title_skips_empty_preloaded_user_message(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_auto_title_skips_empty_message.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO messages(
                session_id,
                workspace_id,
                conversation_id,
                agent_role_id,
                instance_id,
                task_id,
                trace_id,
                role,
                message_json,
                created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-1",
                "default",
                "inst-empty",
                "",
                "inst-empty",
                "task-empty",
                "run-empty",
                "user",
                json.dumps(
                    [
                        {
                            "parts": [
                                {
                                    "part_kind": "user-prompt",
                                    "content": "   \n\t ",
                                }
                            ]
                        }
                    ]
                ),
                "2026-01-02T03:04:05+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    message_repo = MessageRepository(db_path)
    _ = message_repo.append_user_prompt_if_missing(
        session_id="session-1",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="Later message title",
        workspace_id="default",
    )

    sessions = service.list_sessions()

    assert sessions[0].metadata == {
        "title": "Later message title",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
    }


def test_list_sessions_auto_title_skips_system_reminder_user_messages(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_auto_title_skips_reminder.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    message_repo = MessageRepository(db_path)
    _ = message_repo.append_user_prompt_if_missing(
        session_id="session-1",
        instance_id="inst-reminder",
        task_id="task-reminder",
        trace_id="run-reminder",
        content=render_system_reminder("Check todos."),
        workspace_id="default",
    )
    _ = message_repo.append_user_prompt_if_missing(
        session_id="session-1",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="Later real user prompt",
        workspace_id="default",
    )

    sessions = service.list_sessions()

    assert sessions[0].metadata == {
        "title": "Later real user prompt",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
    }


def test_auto_title_does_not_replace_manual_session_title(tmp_path: Path) -> None:
    db_path = tmp_path / "session_auto_title_manual.db"
    run_intent_repo = RunIntentRepository(db_path)
    service = _build_service(db_path, run_intent_repo=run_intent_repo)
    _ = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={
            "title": "Manual title",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
        },
    )
    run_intent_repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("Auto candidate"),
            execution_mode=ExecutionMode.AI,
        ),
    )

    session = service.get_session("session-1")

    assert session.metadata == {
        "title": "Manual title",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
    }


@pytest.mark.parametrize(
    "terminal_status",
    (
        RunRuntimeStatus.COMPLETED,
        RunRuntimeStatus.FAILED,
        RunRuntimeStatus.STOPPED,
    ),
)
def test_list_sessions_projects_latest_terminal_run_status(
    tmp_path: Path,
    terminal_status: RunRuntimeStatus,
) -> None:
    db_path = tmp_path / f"session_terminal_{terminal_status.value}.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    updated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    _ = RunRuntimeRepository(db_path).upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=terminal_status,
            updated_at=updated_at,
        )
    )

    session = service.list_sessions()[0]

    assert session.latest_terminal_run_id == "run-1"
    assert session.latest_terminal_run_status == terminal_status.value
    assert session.latest_terminal_run_updated_at == updated_at
    assert session.has_unread_terminal_run is True


def test_mark_latest_terminal_run_viewed_clears_unread_without_touching_session_time(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_terminal_viewed.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    repository = SessionRepository(db_path)
    original_updated_at = repository.get("session-1").updated_at
    _ = RunRuntimeRepository(db_path).upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            updated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
    )

    service.mark_latest_terminal_run_viewed("session-1")

    stored = repository.get("session-1")
    session = service.get_session("session-1")
    assert stored.last_viewed_terminal_run_id == "run-1"
    assert stored.updated_at == original_updated_at
    assert session.has_unread_terminal_run is False


def test_mark_latest_terminal_run_viewed_noops_without_terminal_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_terminal_viewed_noop.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    service.mark_latest_terminal_run_viewed("session-1")

    stored = SessionRepository(db_path).get("session-1")
    session = service.get_session("session-1")
    assert stored.last_viewed_terminal_run_id is None
    assert session.latest_terminal_run_id is None
    assert session.has_unread_terminal_run is False


def test_running_run_does_not_project_terminal_unread(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_running_no_terminal_unread.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = RunRuntimeRepository(db_path).upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            status=RunRuntimeStatus.RUNNING,
            updated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
    )

    session = service.get_session("session-1")

    assert session.latest_terminal_run_id is None
    assert session.has_unread_terminal_run is False


def test_terminal_projection_ignores_subagent_run_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "session_terminal_excludes_subagent.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    run_repo = RunRuntimeRepository(db_path)
    _ = run_repo.upsert(
        RunRuntimeRecord(
            run_id="run-parent",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            updated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
    )
    _ = run_repo.upsert(
        RunRuntimeRecord(
            run_id="subagent_run_newer",
            session_id="session-1",
            status=RunRuntimeStatus.COMPLETED,
            updated_at=datetime(2026, 1, 2, 3, 5, 5, tzinfo=timezone.utc),
        )
    )

    session = service.list_sessions()[0]
    service.mark_latest_terminal_run_viewed("session-1")
    viewed = service.get_session("session-1")

    assert session.latest_terminal_run_id == "run-parent"
    assert SessionRepository(db_path).get("session-1").last_viewed_terminal_run_id == (
        "run-parent"
    )
    assert viewed.has_unread_terminal_run is False


def test_list_sessions_projects_2000_sessions_under_pressure(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_pressure.db"
    run_intent_repo = RunIntentRepository(db_path)
    service = _build_service(db_path, run_intent_repo=run_intent_repo)
    _ = SessionRepository(db_path)
    _ = RunRuntimeRepository(db_path)
    updated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    session_rows: list[tuple[object, ...]] = []
    intent_rows: list[tuple[object, ...]] = []
    runtime_rows: list[tuple[object, ...]] = []

    for index in range(2000):
        session_id = f"session-{index:04d}"
        run_id = f"run-{index}"
        row_timestamp = (now + timedelta(microseconds=index)).isoformat()
        input_json = ContentPartsAdapter.dump_json(
            content_parts_from_text(f"User request {index}")
        ).decode("utf-8")
        session_rows.append(
            (
                session_id,
                "default",
                "workspace",
                "default",
                json.dumps({}),
                "normal",
                None,
                None,
                None,
                None,
                row_timestamp,
                row_timestamp,
            )
        )
        intent_rows.append(
            (
                run_id,
                session_id,
                f"User request {index}",
                input_json,
                None,
                "conversation",
                None,
                "ai",
                "false",
                "true",
                "false",
                None,
                None,
                None,
                "normal",
                None,
                None,
                row_timestamp,
                row_timestamp,
            )
        )
        if index % 5 == 0:
            runtime_rows.append(
                (
                    run_id,
                    session_id,
                    None,
                    RunRuntimeStatus.COMPLETED.value,
                    "idle",
                    None,
                    None,
                    None,
                    None,
                    None,
                    updated_at.isoformat(),
                    (updated_at + timedelta(seconds=index)).isoformat(),
                )
            )

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO sessions(
                session_id,
                workspace_id,
                project_kind,
                project_id,
                metadata,
                session_mode,
                normal_root_role_id,
                orchestration_preset_id,
                started_at,
                last_viewed_terminal_run_id,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            session_rows,
        )
        conn.executemany(
            """
            INSERT INTO run_intents(
                run_id,
                session_id,
                intent,
                input_json,
                display_input_json,
                run_kind,
                generation_config_json,
                execution_mode,
                yolo,
                reuse_root_instance,
                thinking_enabled,
                thinking_effort,
                target_role_id,
                skills_json,
                session_mode,
                topology_json,
                conversation_context_json,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            intent_rows,
        )
        conn.executemany(
            """
            INSERT INTO run_runtime(
                run_id,
                session_id,
                root_task_id,
                status,
                phase,
                active_instance_id,
                active_task_id,
                active_role_id,
                active_subagent_instance_id,
                last_error,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            runtime_rows,
        )

    _ = service.list_sessions()
    started = time.perf_counter()
    sessions = service.list_sessions()
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert len(sessions) == 2000
    assert sessions[0].metadata["title"].startswith("User request")
    assert any(session.latest_terminal_run_id for session in sessions)
    assert elapsed_ms < 300
