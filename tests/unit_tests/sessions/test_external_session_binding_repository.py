# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from agent_teams.sessions import ExternalSessionBindingRepository


def test_upsert_and_get_binding(tmp_path: Path) -> None:
    repo = ExternalSessionBindingRepository(tmp_path / "bindings.db")

    created = repo.upsert_binding(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-1",
    )
    loaded = repo.get_binding(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
    )

    assert created.session_id == "session-1"
    assert loaded is not None
    assert loaded.session_id == "session-1"


def test_upsert_updates_existing_binding(tmp_path: Path) -> None:
    repo = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    _ = repo.upsert_binding(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-1",
    )

    updated = repo.upsert_binding(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-2",
    )

    assert updated.session_id == "session-2"


def test_external_session_binding_repository_skips_invalid_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "bindings.db"
    repo = ExternalSessionBindingRepository(db_path)
    _ = repo.upsert_binding(
        platform="feishu",
        trigger_id="trigger-valid",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-1",
    )
    _insert_binding_row(
        db_path,
        trigger_id="None",
    )

    bindings = repo.list_by_platform("feishu")

    assert [binding.trigger_id for binding in bindings] == ["trigger-valid"]
    assert (
        repo.get_binding(
            platform="feishu",
            trigger_id="None",
            tenant_key="tenant-1",
            external_chat_id="chat-2",
        )
        is None
    )


def _insert_binding_row(
    db_path: Path,
    *,
    trigger_id: str,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO external_session_bindings(
            platform,
            trigger_id,
            tenant_key,
            external_chat_id,
            session_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "feishu",
            trigger_id,
            "tenant-1",
            "chat-2",
            "session-2",
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()
