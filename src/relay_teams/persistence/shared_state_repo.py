# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
)


class SharedStateRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        self._run_write(
            operation_name="init_tables",
            operation=lambda: self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_state (
                    scope_type  TEXT NOT NULL,
                    scope_id    TEXT NOT NULL,
                    state_key   TEXT NOT NULL,
                    value_json  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at  TEXT,
                    PRIMARY KEY (scope_type, scope_id, state_key)
                )
                """
            ),
        )

    def manage_state(
        self,
        mutation: StateMutation,
        ttl_seconds: int | None = None,
    ) -> None:
        expires_at: str | None = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()
        self._run_write(
            operation_name="manage_state",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO shared_state(scope_type, scope_id, state_key, value_json, updated_at, expires_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(scope_type, scope_id, state_key)
                DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP,
                              expires_at=COALESCE(excluded.expires_at, expires_at)
                """,
                (
                    mutation.scope.scope_type.value,
                    mutation.scope.scope_id,
                    mutation.key,
                    mutation.value_json,
                    expires_at,
                ),
            ),
        )

    async def manage_state_async(
        self, mutation: StateMutation, ttl_seconds: int | None = None
    ) -> None:
        expires_at: str | None = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()

        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                """
                INSERT INTO shared_state(scope_type, scope_id, state_key, value_json, updated_at, expires_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(scope_type, scope_id, state_key)
                DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP,
                              expires_at=COALESCE(excluded.expires_at, expires_at)
                """,
                (
                    mutation.scope.scope_type.value,
                    mutation.scope.scope_id,
                    mutation.key,
                    mutation.value_json,
                    expires_at,
                ),
            )

        await self._run_async_write(
            operation_name="manage_state",
            operation=lambda _conn: operation(),
        )

    def get_state(self, scope: ScopeRef, key: str) -> str | None:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT value_json FROM shared_state
                WHERE scope_type=? AND scope_id=? AND state_key=?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """,
                (scope.scope_type.value, scope.scope_id, key),
            ).fetchone()
        )
        if row is None:
            return None
        return str(row["value_json"])

    async def get_state_async(self, scope: ScopeRef, key: str) -> str | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT value_json FROM shared_state
                WHERE scope_type=? AND scope_id=? AND state_key=?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """,
                (scope.scope_type.value, scope.scope_id, key),
            )
        )
        if row is None:
            return None
        return str(row["value_json"])

    def snapshot(self, scope: ScopeRef) -> tuple[tuple[str, str], ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT state_key, value_json FROM shared_state
                WHERE scope_type=? AND scope_id=?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """,
                (scope.scope_type.value, scope.scope_id),
            ).fetchall()
        )
        return tuple((str(row["state_key"]), str(row["value_json"])) for row in rows)

    async def snapshot_async(self, scope: ScopeRef) -> tuple[tuple[str, str], ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT state_key, value_json FROM shared_state
                WHERE scope_type=? AND scope_id=?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """,
                (scope.scope_type.value, scope.scope_id),
            )
        )
        return tuple((str(row["state_key"]), str(row["value_json"])) for row in rows)

    def snapshot_many(
        self,
        scopes: tuple[ScopeRef, ...],
        *,
        exclude_key_prefixes: tuple[str, ...] = (),
    ) -> tuple[tuple[str, str], ...]:
        merged: dict[str, str] = {}
        for scope in scopes:
            for key, value in self.snapshot(scope):
                if key.startswith(exclude_key_prefixes):
                    continue
                merged[key] = value
        ordered_items = sorted(merged.items(), key=lambda item: item[0])
        return tuple((key, value) for key, value in ordered_items)

    async def snapshot_many_async(
        self,
        scopes: tuple[ScopeRef, ...],
        *,
        exclude_key_prefixes: tuple[str, ...] = (),
    ) -> tuple[tuple[str, str], ...]:
        merged: dict[str, str] = {}
        for scope in scopes:
            for key, value in await self.snapshot_async(scope):
                if key.startswith(exclude_key_prefixes):
                    continue
                merged[key] = value
        ordered_items = sorted(merged.items(), key=lambda item: item[0])
        return tuple((key, value) for key, value in ordered_items)

    def cleanup_expired(self) -> int:
        return self._run_write(
            operation_name="cleanup_expired",
            operation=lambda: (
                self._conn.execute(
                    "DELETE FROM shared_state WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP"
                ).rowcount
            ),
        )

    async def cleanup_expired_async(self) -> int:
        async def operation() -> int:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM shared_state WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP"
            )
            try:
                return cursor.rowcount
            finally:
                await cursor.close()

        return await self._run_async_write(
            operation_name="cleanup_expired",
            operation=lambda _conn: operation(),
        )

    def delete_by_scope_key_prefix(self, scope: ScopeRef, key_prefix: str) -> None:
        self._run_write(
            operation_name="delete_by_scope_key_prefix",
            operation=lambda: self._conn.execute(
                """
                DELETE FROM shared_state
                WHERE scope_type=? AND scope_id=?
                  AND substr(state_key, 1, ?) = ?
                """,
                (
                    scope.scope_type.value,
                    scope.scope_id,
                    len(key_prefix),
                    key_prefix,
                ),
            ),
        )

    async def delete_by_scope_key_prefix_async(
        self, scope: ScopeRef, key_prefix: str
    ) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                """
                DELETE FROM shared_state
                WHERE scope_type=? AND scope_id=?
                  AND substr(state_key, 1, ?) = ?
                """,
                (
                    scope.scope_type.value,
                    scope.scope_id,
                    len(key_prefix),
                    key_prefix,
                ),
            )

        await self._run_async_write(
            operation_name="delete_by_scope_key_prefix",
            operation=lambda _conn: operation(),
        )

    def delete_by_session(
        self,
        session_id: str,
        task_ids: list[str],
        instance_ids: list[str],
        role_scope_ids: list[str] | None = None,
        session_scope_ids: list[str] | None = None,
        conversation_ids: list[str] | None = None,
        workspace_ids: list[str] | None = None,
    ) -> None:
        if not task_ids:
            task_ids = ["__dummy_id__"]
        if not instance_ids:
            instance_ids = ["__dummy_id__"]
        session_scope_values = list(
            dict.fromkeys([session_id, *(session_scope_ids or [])])
        )
        role_scope_values = role_scope_ids or ["__dummy_id__"]
        conversation_values = conversation_ids or ["__dummy_id__"]
        workspace_values = workspace_ids or ["__dummy_id__"]

        session_placeholders = ",".join("?" * len(session_scope_values))
        task_placeholders = ",".join("?" * len(task_ids))
        instance_placeholders = ",".join("?" * len(instance_ids))
        role_placeholders = ",".join("?" * len(role_scope_values))
        conversation_placeholders = ",".join("?" * len(conversation_values))
        workspace_placeholders = ",".join("?" * len(workspace_values))

        self._run_write(
            operation_name="delete_by_session",
            operation=lambda: self._conn.execute(
                f"""
                DELETE FROM shared_state WHERE
                (scope_type=? AND scope_id IN ({session_placeholders})) OR
                (scope_type=? AND scope_id IN ({task_placeholders})) OR
                (scope_type=? AND scope_id IN ({instance_placeholders})) OR
                (scope_type=? AND scope_id IN ({role_placeholders})) OR
                (scope_type=? AND scope_id IN ({conversation_placeholders})) OR
                (scope_type=? AND scope_id IN ({workspace_placeholders}))
                """,
                (
                    ScopeType.SESSION.value,
                    *session_scope_values,
                    ScopeType.TASK.value,
                    *task_ids,
                    ScopeType.INSTANCE.value,
                    *instance_ids,
                    ScopeType.ROLE.value,
                    *role_scope_values,
                    ScopeType.CONVERSATION.value,
                    *conversation_values,
                    ScopeType.WORKSPACE.value,
                    *workspace_values,
                ),
            ),
        )

    async def delete_by_session_async(
        self,
        session_id: str,
        task_ids: list[str],
        instance_ids: list[str],
        role_scope_ids: list[str] | None = None,
        session_scope_ids: list[str] | None = None,
        conversation_ids: list[str] | None = None,
        workspace_ids: list[str] | None = None,
    ) -> None:
        if not task_ids:
            task_ids = ["__dummy_id__"]
        if not instance_ids:
            instance_ids = ["__dummy_id__"]
        session_scope_values = list(
            dict.fromkeys([session_id, *(session_scope_ids or [])])
        )
        role_scope_values = role_scope_ids or ["__dummy_id__"]
        conversation_values = conversation_ids or ["__dummy_id__"]
        workspace_values = workspace_ids or ["__dummy_id__"]

        session_placeholders = ",".join("?" * len(session_scope_values))
        task_placeholders = ",".join("?" * len(task_ids))
        instance_placeholders = ",".join("?" * len(instance_ids))
        role_placeholders = ",".join("?" * len(role_scope_values))
        conversation_placeholders = ",".join("?" * len(conversation_values))
        workspace_placeholders = ",".join("?" * len(workspace_values))

        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                f"""
                DELETE FROM shared_state WHERE
                (scope_type=? AND scope_id IN ({session_placeholders})) OR
                (scope_type=? AND scope_id IN ({task_placeholders})) OR
                (scope_type=? AND scope_id IN ({instance_placeholders})) OR
                (scope_type=? AND scope_id IN ({role_placeholders})) OR
                (scope_type=? AND scope_id IN ({conversation_placeholders})) OR
                (scope_type=? AND scope_id IN ({workspace_placeholders}))
                """,
                (
                    ScopeType.SESSION.value,
                    *session_scope_values,
                    ScopeType.TASK.value,
                    *task_ids,
                    ScopeType.INSTANCE.value,
                    *instance_ids,
                    ScopeType.ROLE.value,
                    *role_scope_values,
                    ScopeType.CONVERSATION.value,
                    *conversation_values,
                    ScopeType.WORKSPACE.value,
                    *workspace_values,
                ),
            )

        await self._run_async_write(
            operation_name="delete_by_session",
            operation=lambda _conn: operation(),
        )

    def delete_for_subagent(
        self,
        *,
        instance_id: str,
        session_scope_id: str,
        role_scope_id: str,
        conversation_id: str,
        task_ids: list[str] | None = None,
    ) -> None:
        task_values = task_ids or ["__dummy_id__"]
        self._run_write(
            operation_name="delete_for_subagent",
            operation=lambda: self._conn.execute(
                """
                DELETE FROM shared_state WHERE
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id IN ({task_placeholders}))
                """.replace(
                    "{task_placeholders}",
                    ",".join("?" for _ in task_values),
                ),
                (
                    ScopeType.INSTANCE.value,
                    instance_id,
                    ScopeType.SESSION.value,
                    session_scope_id,
                    ScopeType.ROLE.value,
                    role_scope_id,
                    ScopeType.CONVERSATION.value,
                    conversation_id,
                    ScopeType.TASK.value,
                    *task_values,
                ),
            ),
        )

    async def delete_for_subagent_async(
        self,
        *,
        instance_id: str,
        session_scope_id: str,
        role_scope_id: str,
        conversation_id: str,
        task_ids: list[str] | None = None,
    ) -> None:
        task_values = task_ids or ["__dummy_id__"]

        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                """
                DELETE FROM shared_state WHERE
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id=?) OR
                (scope_type=? AND scope_id IN ({task_placeholders}))
                """.replace(
                    "{task_placeholders}",
                    ",".join("?" for _ in task_values),
                ),
                (
                    ScopeType.INSTANCE.value,
                    instance_id,
                    ScopeType.SESSION.value,
                    session_scope_id,
                    ScopeType.ROLE.value,
                    role_scope_id,
                    ScopeType.CONVERSATION.value,
                    conversation_id,
                    ScopeType.TASK.value,
                    *task_values,
                ),
            )

        await self._run_async_write(
            operation_name="delete_for_subagent",
            operation=lambda _conn: operation(),
        )


def build_global_scope_ref() -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.GLOBAL, scope_id="global")
