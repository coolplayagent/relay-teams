# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.sessions import ExternalSessionBindingRepository


def test_upsert_and_get_binding(tmp_path: Path) -> None:
    repo = ExternalSessionBindingRepository(tmp_path / "bindings.db")

    created = repo.upsert_binding(
        platform="feishu",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-1",
    )
    loaded = repo.get_binding(
        platform="feishu",
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
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-1",
    )

    updated = repo.upsert_binding(
        platform="feishu",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-2",
    )

    assert updated.session_id == "session-2"
