from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from agent_teams.sessions.session_repository import SessionRepository


def test_list_all_tolerates_blank_session_metadata_json(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_blank_metadata.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-blank",
        metadata_json="",
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-blank"
    assert records[0].metadata == {}


def test_list_all_tolerates_invalid_session_metadata_json(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_invalid_metadata.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-invalid",
        metadata_json="{",
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-invalid"
    assert records[0].metadata == {}


def test_list_all_filters_non_string_metadata_values(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_mixed_metadata.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-mixed",
        metadata_json=(
            '{"title":"Ops Run","retries":2,"enabled":true,"payload":{"bad":1}}'
        ),
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].metadata == {
        "title": "Ops Run",
        "retries": "2",
        "enabled": "True",
    }


def test_list_all_tolerates_blank_started_at(tmp_path: Path) -> None:
    db_path = tmp_path / "session_repository_blank_started_at.db"
    repository = SessionRepository(db_path)
    _insert_session_row(
        db_path,
        session_id="session-blank-started-at",
        metadata_json="{}",
        started_at="",
    )

    records = repository.list_all()

    assert len(records) == 1
    assert records[0].session_id == "session-blank-started-at"
    assert records[0].started_at is None
    assert records[0].can_switch_mode is True


def test_repository_init_normalizes_blank_started_at_for_mark_started(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_repository_started_at_cleanup.db"
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL DEFAULT '',
            project_kind TEXT NOT NULL DEFAULT 'workspace',
            project_id TEXT NOT NULL DEFAULT '',
            metadata   TEXT NOT NULL,
            session_mode TEXT NOT NULL DEFAULT 'normal',
            normal_root_role_id TEXT,
            orchestration_preset_id TEXT,
            started_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
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
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "session-preexisting-blank-started-at",
            "default",
            "workspace",
            "default",
            "{}",
            "normal",
            None,
            None,
            "",
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()

    repository = SessionRepository(db_path)

    record = repository.mark_started("session-preexisting-blank-started-at")

    assert record.started_at is not None
    assert record.can_switch_mode is False


def _insert_session_row(
    db_path: Path,
    *,
    session_id: str,
    metadata_json: str,
    started_at: str | None = None,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
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
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "default",
            "workspace",
            "default",
            metadata_json,
            "normal",
            None,
            None,
            started_at,
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()
