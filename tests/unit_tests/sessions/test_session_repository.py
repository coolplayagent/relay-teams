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


def _insert_session_row(
    db_path: Path,
    *,
    session_id: str,
    metadata_json: str,
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
            None,
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()
