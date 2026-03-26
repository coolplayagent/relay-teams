# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.roles.memory_models import MemoryProfile
from agent_teams.roles.temporary_role_models import TemporaryRoleRecord
from agent_teams.roles.temporary_role_models import TemporaryRoleSpec


class TemporaryRoleRepository:
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
                CREATE TABLE IF NOT EXISTS temporary_roles (
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    version TEXT NOT NULL,
                    tools_json TEXT NOT NULL,
                    mcp_servers_json TEXT NOT NULL,
                    skills_json TEXT NOT NULL,
                    model_profile TEXT NOT NULL,
                    bound_agent_id TEXT,
                    memory_profile_json TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    template_role_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, role_id)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_temp_roles_run ON temporary_roles(run_id, created_at ASC)"
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="TemporaryRoleRepository",
            operation_name="init_tables",
        )

    def upsert(self, record: TemporaryRoleRecord) -> TemporaryRoleRecord:
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO temporary_roles(
                    run_id, session_id, role_id, source, name, description, version,
                    tools_json, mcp_servers_json, skills_json, model_profile,
                    bound_agent_id, memory_profile_json, system_prompt, template_role_id,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, role_id)
                DO UPDATE SET
                    session_id=excluded.session_id,
                    source=excluded.source,
                    name=excluded.name,
                    description=excluded.description,
                    version=excluded.version,
                    tools_json=excluded.tools_json,
                    mcp_servers_json=excluded.mcp_servers_json,
                    skills_json=excluded.skills_json,
                    model_profile=excluded.model_profile,
                    bound_agent_id=excluded.bound_agent_id,
                    memory_profile_json=excluded.memory_profile_json,
                    system_prompt=excluded.system_prompt,
                    template_role_id=excluded.template_role_id,
                    updated_at=excluded.updated_at
                """,
                (
                    record.run_id,
                    record.session_id,
                    record.role.role_id,
                    record.source,
                    record.role.name,
                    record.role.description,
                    record.role.version,
                    _json_tuple(record.role.tools),
                    _json_tuple(record.role.mcp_servers),
                    _json_tuple(record.role.skills),
                    record.role.model_profile,
                    record.role.bound_agent_id,
                    record.role.memory_profile.model_dump_json(),
                    record.role.system_prompt,
                    record.role.template_role_id,
                    record.created_at.isoformat(),
                    now,
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="TemporaryRoleRepository",
            operation_name="upsert",
        )
        return self.get(run_id=record.run_id, role_id=record.role.role_id)

    def get(self, *, run_id: str, role_id: str) -> TemporaryRoleRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM temporary_roles WHERE run_id=? AND role_id=?",
                (run_id, role_id),
            ).fetchone()
        if row is None:
            raise KeyError(
                f"Unknown temporary role: run_id={run_id}, role_id={role_id}"
            )
        return self._to_record(row)

    def list_by_run(self, run_id: str) -> tuple[TemporaryRoleRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM temporary_roles WHERE run_id=? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def delete_by_run(self, run_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM temporary_roles WHERE run_id=?", (run_id,)
            ),
            lock=self._lock,
            repository_name="TemporaryRoleRepository",
            operation_name="delete_by_run",
        )

    def _to_record(self, row: sqlite3.Row) -> TemporaryRoleRecord:
        return TemporaryRoleRecord(
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            source=str(row["source"]),
            role=TemporaryRoleSpec(
                role_id=str(row["role_id"]),
                name=str(row["name"]),
                description=str(row["description"]),
                version=str(row["version"]),
                tools=tuple(_json_load_tuple(str(row["tools_json"]))),
                mcp_servers=tuple(_json_load_tuple(str(row["mcp_servers_json"]))),
                skills=tuple(_json_load_tuple(str(row["skills_json"]))),
                model_profile=str(row["model_profile"]),
                bound_agent_id=(
                    str(row["bound_agent_id"])
                    if row["bound_agent_id"] is not None
                    else None
                ),
                memory_profile=_memory_profile_from_json(
                    str(row["memory_profile_json"])
                ),
                system_prompt=str(row["system_prompt"]),
                template_role_id=(
                    str(row["template_role_id"])
                    if row["template_role_id"] is not None
                    else None
                ),
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


def _json_tuple(values: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(values), ensure_ascii=False)


def _json_load_tuple(raw: str) -> tuple[str, ...]:
    import json

    loaded = json.loads(raw)
    if not isinstance(loaded, list):
        return ()
    return tuple(str(item) for item in loaded)


def _memory_profile_from_json(raw: str) -> "MemoryProfile":
    import json

    payload = json.loads(raw)
    return MemoryProfile.model_validate(payload)
