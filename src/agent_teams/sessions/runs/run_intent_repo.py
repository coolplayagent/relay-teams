from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from agent_teams.media import (
    ContentPartsAdapter,
    content_parts_from_text,
    content_parts_to_text,
    text_part,
)
from agent_teams.sessions.runs.enums import ExecutionMode
from agent_teams.sessions.runs.run_models import (
    IntentInput,
    RuntimePromptConversationContext,
    MediaGenerationConfig,
    RunThinkingConfig,
    RunKind,
    RunTopologySnapshot,
)
from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.session_models import SessionMode

type _ThinkingEffort = Literal["minimal", "low", "medium", "high"] | None
_MediaGenerationConfigAdapter = TypeAdapter(MediaGenerationConfig)


class RunIntentRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_intents (
                run_id         TEXT PRIMARY KEY,
                session_id     TEXT NOT NULL,
                intent         TEXT NOT NULL,
                input_json     TEXT,
                run_kind       TEXT NOT NULL DEFAULT 'conversation',
                generation_config_json TEXT,
                execution_mode TEXT NOT NULL,
                yolo           TEXT NOT NULL DEFAULT 'false',
                reuse_root_instance TEXT NOT NULL DEFAULT 'true',
                thinking_enabled TEXT NOT NULL DEFAULT 'false',
                thinking_effort TEXT,
                target_role_id TEXT,
                session_mode TEXT NOT NULL DEFAULT 'normal',
                topology_json TEXT,
                conversation_context_json TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
            """
        )
        columns = [
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(run_intents)").fetchall()
        ]
        if "yolo" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN yolo TEXT NOT NULL DEFAULT 'false'"
            )
            if "approval_mode" in columns:
                self._conn.execute(
                    """
                    UPDATE run_intents
                    SET yolo = CASE
                        WHEN approval_mode = 'yolo' THEN 'true'
                        ELSE 'false'
                    END
                    """
                )
        if "thinking_enabled" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN thinking_enabled TEXT NOT NULL DEFAULT 'false'"
            )
        if "reuse_root_instance" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN reuse_root_instance TEXT NOT NULL DEFAULT 'true'"
            )
        if "input_json" not in columns:
            self._conn.execute("ALTER TABLE run_intents ADD COLUMN input_json TEXT")
        if "run_kind" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'conversation'"
            )
        if "generation_config_json" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN generation_config_json TEXT"
            )
        if "thinking_effort" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN thinking_effort TEXT"
            )
        if "session_mode" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN session_mode TEXT NOT NULL DEFAULT 'normal'"
            )
        if "target_role_id" not in columns:
            self._conn.execute("ALTER TABLE run_intents ADD COLUMN target_role_id TEXT")
        if "topology_json" not in columns:
            self._conn.execute("ALTER TABLE run_intents ADD COLUMN topology_json TEXT")
        if "conversation_context_json" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN conversation_context_json TEXT"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id)"
        )
        self._conn.commit()

    def upsert(self, *, run_id: str, session_id: str, intent: IntentInput) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
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
            ON CONFLICT(run_id)
            DO UPDATE SET
                session_id=excluded.session_id,
                intent=excluded.intent,
                input_json=excluded.input_json,
                run_kind=excluded.run_kind,
                generation_config_json=excluded.generation_config_json,
                execution_mode=excluded.execution_mode,
                yolo=excluded.yolo,
                reuse_root_instance=excluded.reuse_root_instance,
                thinking_enabled=excluded.thinking_enabled,
                thinking_effort=excluded.thinking_effort,
                target_role_id=excluded.target_role_id,
                session_mode=excluded.session_mode,
                topology_json=excluded.topology_json,
                conversation_context_json=excluded.conversation_context_json,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                session_id,
                intent.intent,
                ContentPartsAdapter.dump_json(intent.input).decode("utf-8"),
                intent.run_kind.value,
                (
                    intent.generation_config.model_dump_json()
                    if intent.generation_config is not None
                    else None
                ),
                intent.execution_mode.value,
                "true" if intent.yolo else "false",
                "true" if intent.reuse_root_instance else "false",
                "true" if intent.thinking.enabled else "false",
                intent.thinking.effort,
                intent.target_role_id,
                intent.session_mode.value,
                (
                    intent.topology.model_dump_json()
                    if intent.topology is not None
                    else None
                ),
                (
                    intent.conversation_context.model_dump_json()
                    if intent.conversation_context is not None
                    else None
                ),
                now,
                now,
            ),
        )
        self._conn.commit()

    def append_followup(self, *, run_id: str, content: str) -> None:
        row = self._conn.execute(
            "SELECT intent, input_json FROM run_intents WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        current_parts = _coerce_input_parts(row["input_json"], row["intent"])
        next_part = text_part(content)
        next_parts = (
            current_parts if next_part is None else current_parts + (next_part,)
        )
        next_intent = content_parts_to_text(next_parts)
        self._conn.execute(
            """
            UPDATE run_intents
            SET intent=?, input_json=?, updated_at=?
            WHERE run_id=?
            """,
            (
                next_intent,
                ContentPartsAdapter.dump_json(next_parts).decode("utf-8"),
                datetime.now(tz=timezone.utc).isoformat(),
                run_id,
            ),
        )
        self._conn.commit()

    def get(self, run_id: str) -> IntentInput:
        row = self._conn.execute(
            """
            SELECT
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
                conversation_context_json
            FROM run_intents
            WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        return IntentInput(
            session_id=str(row["session_id"]),
            input=_coerce_input_parts(row["input_json"], row["intent"]),
            run_kind=RunKind(str(row["run_kind"] or RunKind.CONVERSATION.value)),
            generation_config=_coerce_generation_config(row["generation_config_json"]),
            execution_mode=ExecutionMode(str(row["execution_mode"])),
            yolo=str(row["yolo"]).strip().lower() == "true",
            reuse_root_instance=(
                str(row["reuse_root_instance"]).strip().lower() != "false"
            ),
            thinking=RunThinkingConfig(
                enabled=str(row["thinking_enabled"]).strip().lower() == "true",
                effort=_coerce_thinking_effort(row["thinking_effort"]),
            ),
            target_role_id=(
                str(row["target_role_id"])
                if row["target_role_id"] is not None
                else None
            ),
            session_mode=SessionMode(str(row["session_mode"] or "normal")),
            topology=_coerce_topology(row["topology_json"]),
            conversation_context=_coerce_conversation_context(
                row["conversation_context_json"]
            ),
        )


def _coerce_thinking_effort(
    value: object,
) -> _ThinkingEffort:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized == "minimal":
        return "minimal"
    if normalized == "low":
        return "low"
    if normalized == "medium":
        return "medium"
    if normalized == "high":
        return "high"
    return None


def _coerce_topology(value: object) -> RunTopologySnapshot | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return RunTopologySnapshot.model_validate_json(value)


def _coerce_conversation_context(
    value: object,
) -> RuntimePromptConversationContext | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return RuntimePromptConversationContext.model_validate_json(value)


def _coerce_input_parts(
    input_json: object,
    legacy_intent: object,
):
    if isinstance(input_json, str) and input_json.strip():
        return ContentPartsAdapter.validate_json(input_json)
    if isinstance(legacy_intent, str):
        return content_parts_from_text(legacy_intent)
    return ()


def _coerce_generation_config(value: object) -> MediaGenerationConfig | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _MediaGenerationConfigAdapter.validate_json(value)
