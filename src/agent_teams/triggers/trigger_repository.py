from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from agent_teams.triggers.trigger_models import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerDefinition,
    TriggerEventRecord,
    TriggerEventStatus,
    TriggerSourceType,
    TriggerStatus,
)
from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry


class TriggerNameConflictError(ValueError):
    pass


class TriggerEventDuplicateError(ValueError):
    def __init__(self, trigger_id: str, event_key: str) -> None:
        super().__init__(
            f"Duplicate trigger event for trigger_id={trigger_id}, event_key={event_key}"
        )
        self.trigger_id = trigger_id
        self.event_key = event_key


class TriggerRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS triggers (
                    trigger_id         TEXT PRIMARY KEY,
                    name               TEXT NOT NULL UNIQUE,
                    display_name       TEXT NOT NULL,
                    source_type        TEXT NOT NULL,
                    status             TEXT NOT NULL,
                    public_token       TEXT UNIQUE,
                    source_config_json TEXT NOT NULL,
                    auth_policies_json TEXT NOT NULL,
                    target_config_json TEXT,
                    created_at         TEXT NOT NULL,
                    updated_at         TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_triggers_source_type ON triggers(source_type)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_triggers_status ON triggers(status)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_events (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id           TEXT NOT NULL UNIQUE,
                    trigger_id         TEXT NOT NULL,
                    trigger_name       TEXT NOT NULL,
                    source_type        TEXT NOT NULL,
                    event_key          TEXT,
                    status             TEXT NOT NULL,
                    received_at        TEXT NOT NULL,
                    occurred_at        TEXT,
                    payload_json       TEXT NOT NULL,
                    metadata_json      TEXT NOT NULL,
                    headers_json       TEXT NOT NULL,
                    remote_addr        TEXT,
                    auth_mode          TEXT,
                    auth_result        TEXT NOT NULL,
                    auth_reason        TEXT,
                    FOREIGN KEY(trigger_id) REFERENCES triggers(trigger_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_trigger_events_key
                ON trigger_events(trigger_id, event_key)
                WHERE event_key IS NOT NULL
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trigger_events_trigger
                ON trigger_events(trigger_id, id DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trigger_events_status
                ON trigger_events(status, id DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="TriggerRepository",
            operation_name="init_tables",
        )

    def create_trigger(self, trigger: TriggerDefinition) -> TriggerDefinition:
        try:
            run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO triggers(
                        trigger_id, name, display_name, source_type, status, public_token,
                        source_config_json, auth_policies_json, target_config_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trigger.trigger_id,
                        trigger.name,
                        trigger.display_name,
                        trigger.source_type.value,
                        trigger.status.value,
                        trigger.public_token,
                        json.dumps(trigger.source_config),
                        json.dumps(
                            [policy.model_dump() for policy in trigger.auth_policies]
                        ),
                        json.dumps(trigger.target_config)
                        if trigger.target_config is not None
                        else None,
                        trigger.created_at.isoformat(),
                        trigger.updated_at.isoformat(),
                    ),
                ),
                lock=self._lock,
                repository_name="TriggerRepository",
                operation_name="create_trigger",
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if (
                "triggers.name" in message
                or "unique constraint failed: triggers.name" in message
            ):
                raise TriggerNameConflictError(
                    f"Trigger name already exists: {trigger.name}"
                ) from exc
            raise
        return trigger

    def update_trigger(self, trigger: TriggerDefinition) -> TriggerDefinition:
        try:
            run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=lambda: self._conn.execute(
                    """
                    UPDATE triggers
                    SET name=?,
                        display_name=?,
                        source_type=?,
                        status=?,
                        public_token=?,
                        source_config_json=?,
                        auth_policies_json=?,
                        target_config_json=?,
                        updated_at=?
                    WHERE trigger_id=?
                    """,
                    (
                        trigger.name,
                        trigger.display_name,
                        trigger.source_type.value,
                        trigger.status.value,
                        trigger.public_token,
                        json.dumps(trigger.source_config),
                        json.dumps(
                            [policy.model_dump() for policy in trigger.auth_policies]
                        ),
                        json.dumps(trigger.target_config)
                        if trigger.target_config is not None
                        else None,
                        trigger.updated_at.isoformat(),
                        trigger.trigger_id,
                    ),
                ),
                lock=self._lock,
                repository_name="TriggerRepository",
                operation_name="update_trigger",
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if (
                "triggers.name" in message
                or "unique constraint failed: triggers.name" in message
            ):
                raise TriggerNameConflictError(
                    f"Trigger name already exists: {trigger.name}"
                ) from exc
            raise
        return trigger

    def get_trigger(self, trigger_id: str) -> TriggerDefinition:
        row = self._conn.execute(
            "SELECT * FROM triggers WHERE trigger_id=?",
            (trigger_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown trigger_id: {trigger_id}")
        return self._row_to_trigger(row)

    def get_trigger_by_name(self, name: str) -> TriggerDefinition:
        row = self._conn.execute(
            "SELECT * FROM triggers WHERE name=?",
            (name,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown trigger name: {name}")
        return self._row_to_trigger(row)

    def get_trigger_by_public_token(self, public_token: str) -> TriggerDefinition:
        row = self._conn.execute(
            "SELECT * FROM triggers WHERE public_token=?",
            (public_token,),
        ).fetchone()
        if row is None:
            raise KeyError("Unknown webhook token")
        return self._row_to_trigger(row)

    def list_triggers(self) -> tuple[TriggerDefinition, ...]:
        rows = self._conn.execute(
            "SELECT * FROM triggers ORDER BY created_at DESC"
        ).fetchall()
        return tuple(self._row_to_trigger(row) for row in rows)

    def delete_trigger(self, trigger_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM triggers WHERE trigger_id=?", (trigger_id,)
            ),
            lock=self._lock,
            repository_name="TriggerRepository",
            operation_name="delete_trigger",
        )

    def delete_events_by_trigger(self, trigger_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM trigger_events WHERE trigger_id=?", (trigger_id,)
            ),
            lock=self._lock,
            repository_name="TriggerRepository",
            operation_name="delete_events_by_trigger",
        )

    def create_event(self, event: TriggerEventRecord) -> TriggerEventRecord:
        try:
            cursor = run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO trigger_events(
                        event_id, trigger_id, trigger_name, source_type, event_key,
                        status, received_at, occurred_at, payload_json, metadata_json,
                        headers_json, remote_addr, auth_mode, auth_result, auth_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.trigger_id,
                        event.trigger_name,
                        event.source_type.value,
                        event.event_key,
                        event.status.value,
                        event.received_at.isoformat(),
                        event.occurred_at.isoformat() if event.occurred_at else None,
                        json.dumps(event.payload),
                        json.dumps(event.metadata),
                        json.dumps(event.headers),
                        event.remote_addr,
                        event.auth_mode.value if event.auth_mode else None,
                        event.auth_result,
                        event.auth_reason,
                    ),
                ),
                lock=self._lock,
                repository_name="TriggerRepository",
                operation_name="create_event",
            )
            sequence_id = cursor.lastrowid
            if not isinstance(sequence_id, int):
                raise RuntimeError("Failed to resolve inserted trigger event row id")
            return event.model_copy(update={"sequence_id": sequence_id})
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if (
                "uq_trigger_events_key" in message
                or "trigger_events.trigger_id, trigger_events.event_key" in message
            ) and event.event_key is not None:
                raise TriggerEventDuplicateError(
                    trigger_id=event.trigger_id,
                    event_key=event.event_key,
                ) from exc
            raise

    def get_event(self, event_id: str) -> TriggerEventRecord:
        row = self._conn.execute(
            "SELECT * FROM trigger_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown trigger event id: {event_id}")
        return self._row_to_event(row)

    def get_event_by_key(self, trigger_id: str, event_key: str) -> TriggerEventRecord:
        row = self._conn.execute(
            """
            SELECT * FROM trigger_events
            WHERE trigger_id=? AND event_key=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (trigger_id, event_key),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Unknown trigger event for trigger_id={trigger_id}, event_key={event_key}"
            )
        return self._row_to_event(row)

    def list_events_by_trigger(
        self,
        trigger_id: str,
        *,
        limit: int = 50,
        cursor_event_id: str | None = None,
    ) -> tuple[tuple[TriggerEventRecord, ...], str | None]:
        safe_limit = max(1, min(limit, 100))
        if cursor_event_id is None:
            rows = self._conn.execute(
                """
                SELECT * FROM trigger_events
                WHERE trigger_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (trigger_id, safe_limit),
            ).fetchall()
        else:
            cursor_row = self._conn.execute(
                "SELECT id FROM trigger_events WHERE event_id=?",
                (cursor_event_id,),
            ).fetchone()
            if cursor_row is None:
                raise KeyError(f"Unknown cursor event id: {cursor_event_id}")
            cursor_id = int(cursor_row["id"])
            rows = self._conn.execute(
                """
                SELECT * FROM trigger_events
                WHERE trigger_id=? AND id < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (trigger_id, cursor_id, safe_limit),
            ).fetchall()

        records = tuple(self._row_to_event(row) for row in rows)
        next_cursor = records[-1].event_id if len(records) == safe_limit else None
        return records, next_cursor

    def _row_to_trigger(self, row: sqlite3.Row) -> TriggerDefinition:
        auth_policies_raw = json.loads(str(row["auth_policies_json"]))
        auth_policies = tuple(
            TriggerAuthPolicy.model_validate(item) for item in auth_policies_raw
        )
        target_config_raw = row["target_config_json"]
        target_config = (
            json.loads(str(target_config_raw))
            if target_config_raw is not None
            else None
        )
        return TriggerDefinition(
            trigger_id=str(row["trigger_id"]),
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            source_type=TriggerSourceType(str(row["source_type"])),
            status=TriggerStatus(str(row["status"])),
            public_token=str(row["public_token"])
            if row["public_token"] is not None
            else None,
            source_config=json.loads(str(row["source_config_json"])),
            auth_policies=auth_policies,
            target_config=target_config,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _row_to_event(self, row: sqlite3.Row) -> TriggerEventRecord:
        return TriggerEventRecord(
            sequence_id=int(row["id"]),
            event_id=str(row["event_id"]),
            trigger_id=str(row["trigger_id"]),
            trigger_name=str(row["trigger_name"]),
            source_type=TriggerSourceType(str(row["source_type"])),
            event_key=str(row["event_key"]) if row["event_key"] is not None else None,
            status=TriggerEventStatus(str(row["status"])),
            received_at=datetime.fromisoformat(str(row["received_at"])),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"]))
            if row["occurred_at"] is not None
            else None,
            payload=json.loads(str(row["payload_json"])),
            metadata=json.loads(str(row["metadata_json"])),
            headers=json.loads(str(row["headers_json"])),
            remote_addr=str(row["remote_addr"])
            if row["remote_addr"] is not None
            else None,
            auth_mode=TriggerAuthMode(str(row["auth_mode"]))
            if row["auth_mode"] is not None
            else None,
            auth_result=str(row["auth_result"]),
            auth_reason=str(row["auth_reason"])
            if row["auth_reason"] is not None
            else None,
        )
