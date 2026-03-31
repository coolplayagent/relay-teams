# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path
import threading
import time

import pytest

from agent_teams.media import content_parts_from_text
from agent_teams.sessions.runs.enums import ExecutionMode
from agent_teams.sessions.runs.run_models import (
    IntentInput,
    RunThinkingConfig,
    RunTopologySnapshot,
)
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.session_models import SessionMode


def test_run_intent_repo_round_trips_yolo(tmp_path: Path) -> None:
    db_path = tmp_path / "run_intent.db"
    repo = RunIntentRepository(db_path)

    repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("ship it"),
            execution_mode=ExecutionMode.AI,
            yolo=True,
        ),
    )

    record = repo.get("run-1")

    assert record.intent == "ship it"
    assert record.execution_mode == ExecutionMode.AI
    assert record.yolo is True


def test_run_intent_repo_does_not_backfill_yolo_from_legacy_approval_mode(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_intent_legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE run_intents (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            intent TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            approval_mode TEXT NOT NULL DEFAULT 'standard',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO run_intents(
            run_id, session_id, intent, execution_mode, approval_mode, created_at, updated_at
        )
        VALUES('run-1', 'session-1', 'ship it', 'ai', 'yolo', '2026-03-20T00:00:00Z', '2026-03-20T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()

    record = RunIntentRepository(db_path).get("run-1")

    assert record.yolo is False


def test_run_intent_repo_uses_fallback_session_id_for_legacy_none_like_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_intent_legacy_session.db"
    repo = RunIntentRepository(db_path)
    now = "2026-03-20T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO run_intents(
            run_id,
            session_id,
            intent,
            input_json,
            run_kind,
            generation_config_json,
            execution_mode,
            yolo,
            reuse_root_instance,
            thinking_enabled,
            thinking_effort,
            target_role_id,
            session_mode,
            topology_json,
            conversation_context_json,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-legacy",
            "None",
            "ship it",
            None,
            "conversation",
            None,
            "ai",
            "false",
            "true",
            "false",
            None,
            "None",
            "normal",
            None,
            None,
            now,
            now,
        ),
    )
    repo._conn.commit()

    record = repo.get("run-legacy", fallback_session_id="session-1")

    assert record.session_id == "session-1"
    assert record.target_role_id is None


def test_run_intent_repo_raises_key_error_for_unrecoverable_legacy_session_id(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_intent_unrecoverable_session.db"
    repo = RunIntentRepository(db_path)
    now = "2026-03-20T00:00:00Z"
    repo._conn.execute(
        """
        INSERT INTO run_intents(
            run_id,
            session_id,
            intent,
            input_json,
            run_kind,
            generation_config_json,
            execution_mode,
            yolo,
            reuse_root_instance,
            thinking_enabled,
            thinking_effort,
            target_role_id,
            session_mode,
            topology_json,
            conversation_context_json,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-unrecoverable",
            "None",
            "ship it",
            None,
            "conversation",
            None,
            "ai",
            "false",
            "true",
            "false",
            None,
            None,
            "normal",
            None,
            None,
            now,
            now,
        ),
    )
    repo._conn.commit()

    with pytest.raises(KeyError):
        repo.get("run-unrecoverable")
    with pytest.raises(KeyError):
        repo.get("run-unrecoverable", fallback_session_id="null")


def test_run_intent_repo_round_trips_thinking_config(tmp_path: Path) -> None:
    db_path = tmp_path / "run_intent_thinking.db"
    repo = RunIntentRepository(db_path)

    repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("ship it"),
            execution_mode=ExecutionMode.AI,
            yolo=False,
            thinking=RunThinkingConfig(enabled=True, effort="medium"),
        ),
    )

    record = repo.get("run-1")

    assert record.thinking.enabled is True
    assert record.thinking.effort == "medium"


def test_run_intent_repo_round_trips_session_topology(tmp_path: Path) -> None:
    db_path = tmp_path / "run_intent_topology.db"
    repo = RunIntentRepository(db_path)

    repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("ship it"),
            execution_mode=ExecutionMode.AI,
            session_mode=SessionMode.ORCHESTRATION,
            topology=RunTopologySnapshot(
                session_mode=SessionMode.ORCHESTRATION,
                main_agent_role_id="MainAgent",
                normal_root_role_id="MainAgent",
                coordinator_role_id="Coordinator",
                orchestration_preset_id="default",
                orchestration_prompt="Delegate by capability.",
                allowed_role_ids=("writer", "reviewer"),
            ),
        ),
    )

    record = repo.get("run-1")

    assert record.session_mode == SessionMode.ORCHESTRATION
    assert record.topology is not None
    assert record.topology.orchestration_preset_id == "default"
    assert record.topology.allowed_role_ids == ("writer", "reviewer")


def test_run_intent_repo_upsert_retries_transient_write_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "run_intent_retry_upsert.db"
    repo = RunIntentRepository(db_path)
    repo._conn.execute("PRAGMA busy_timeout = 0")

    blocker = sqlite3.connect(db_path, check_same_thread=False)
    blocker.execute("PRAGMA busy_timeout = 0")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("SELECT 1")

    released = threading.Event()

    def release_lock() -> None:
        time.sleep(0.05)
        blocker.commit()
        blocker.close()
        released.set()

    thread = threading.Thread(target=release_lock)
    thread.start()

    repo.upsert(
        run_id="run-retry",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("ship it"),
            execution_mode=ExecutionMode.AI,
            yolo=False,
        ),
    )

    thread.join(timeout=1)

    assert released.is_set()
    assert repo.get("run-retry").intent == "ship it"


def test_run_intent_repo_append_followup_retries_transient_write_lock(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_intent_retry_followup.db"
    repo = RunIntentRepository(db_path)
    repo.upsert(
        run_id="run-followup",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("ship it"),
            execution_mode=ExecutionMode.AI,
            yolo=False,
        ),
    )
    repo._conn.execute("PRAGMA busy_timeout = 0")

    blocker = sqlite3.connect(db_path, check_same_thread=False)
    blocker.execute("PRAGMA busy_timeout = 0")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute(
        "UPDATE run_intents SET updated_at=updated_at WHERE run_id=?",
        ("run-followup",),
    )

    released = threading.Event()

    def release_lock() -> None:
        time.sleep(0.05)
        blocker.commit()
        blocker.close()
        released.set()

    thread = threading.Thread(target=release_lock)
    thread.start()

    repo.append_followup(run_id="run-followup", content="and validate it")

    thread.join(timeout=1)

    record = repo.get("run-followup")
    assert released.is_set()
    assert record.intent == "ship it\n\nand validate it"
