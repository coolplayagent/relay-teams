# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from relay_teams.persistence.db import run_async_blocking
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
        run_async_blocking(self._init_tables_async())

    async def _init_tables_async(self) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
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
            )

        await self._run_async_write(
            operation_name="init_tables",
            operation=lambda _conn: operation(),
        )

    def manage_state(
        self,
        mutation: StateMutation,
        ttl_seconds: int | None = None,
    ) -> None:
        run_async_blocking(self.manage_state_async(mutation, ttl_seconds=ttl_seconds))

    def manage_states(
        self,
        mutations: tuple[StateMutation, ...],
        ttl_seconds: int | None = None,
    ) -> None:
        if not mutations:
            return
        expires_at: str | None = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()
        parameters = tuple(
            (
                mutation.scope.scope_type.value,
                mutation.scope.scope_id,
                mutation.key,
                mutation.value_json,
                expires_at,
            )
            for mutation in mutations
        )

        def operation() -> None:
            cursor = self._conn.executemany(
                """
                INSERT INTO shared_state(scope_type, scope_id, state_key, value_json, updated_at, expires_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(scope_type, scope_id, state_key)
                DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP,
                              expires_at=COALESCE(excluded.expires_at, expires_at)
                """,
                parameters,
            )
            cursor.close()

        self._run_write(operation_name="manage_states", operation=operation)

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
            cursor = await conn.execute(
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
            await cursor.close()

        await self._run_async_write(
            operation_name="manage_state",
            operation=lambda _conn: operation(),
        )

    async def manage_states_async(
        self,
        mutations: tuple[StateMutation, ...],
        ttl_seconds: int | None = None,
    ) -> None:
        if not mutations:
            return
        expires_at: str | None = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()
        parameters = tuple(
            (
                mutation.scope.scope_type.value,
                mutation.scope.scope_id,
                mutation.key,
                mutation.value_json,
                expires_at,
            )
            for mutation in mutations
        )

        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.executemany(
                """
                INSERT INTO shared_state(scope_type, scope_id, state_key, value_json, updated_at, expires_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(scope_type, scope_id, state_key)
                DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP,
                              expires_at=COALESCE(excluded.expires_at, expires_at)
                """,
                parameters,
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="manage_states",
            operation=lambda _conn: operation(),
        )

    async def update_state_async(
        self,
        *,
        scope: ScopeRef,
        key: str,
        update: Callable[[str | None], str],
        ttl_seconds: int | None = None,
    ) -> str:
        expires_at: str | None = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()

        async def operation() -> str:
            conn = await self._get_async_conn()
            row = await async_fetchone(
                conn,
                """
                SELECT value_json FROM shared_state
                WHERE scope_type=? AND scope_id=? AND state_key=?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """,
                (scope.scope_type.value, scope.scope_id, key),
            )
            current_value = None if row is None else str(row["value_json"])
            next_value = update(current_value)
            await conn.execute(
                """
                INSERT INTO shared_state(scope_type, scope_id, state_key, value_json, updated_at, expires_at)
                VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(scope_type, scope_id, state_key)
                DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP,
                              expires_at=COALESCE(excluded.expires_at, expires_at)
                """,
                (
                    scope.scope_type.value,
                    scope.scope_id,
                    key,
                    next_value,
                    expires_at,
                ),
            )
            return next_value

        return await self._run_async_write(
            operation_name="update_state",
            operation=lambda _conn: operation(),
        )

    def get_state(self, scope: ScopeRef, key: str) -> str | None:
        return run_async_blocking(self.get_state_async(scope, key))

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

    async def get_states_async(
        self,
        scope: ScopeRef,
        keys: tuple[str, ...],
    ) -> tuple[tuple[str, str], ...]:
        normalized_keys = tuple(
            dict.fromkeys(key.strip() for key in keys if key.strip())
        )
        if not normalized_keys:
            return ()
        rows_by_key: dict[str, str] = {}
        chunk_size = 250
        for index in range(0, len(normalized_keys), chunk_size):
            chunk = normalized_keys[index : index + chunk_size]
            placeholders = ", ".join("?" for _key in chunk)
            rows = await self._run_async_read(
                lambda conn, chunk_keys=chunk, chunk_placeholders=placeholders: (
                    async_fetchall(
                        conn,
                        f"""
                    SELECT state_key, value_json FROM shared_state
                    WHERE scope_type=? AND scope_id=? AND state_key IN ({chunk_placeholders})
                      AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                    """,
                        (
                            scope.scope_type.value,
                            scope.scope_id,
                            *chunk_keys,
                        ),
                    )
                )
            )
            for row in rows:
                rows_by_key[str(row["state_key"])] = str(row["value_json"])
        return tuple(
            (key, rows_by_key[key]) for key in normalized_keys if key in rows_by_key
        )

    def snapshot(self, scope: ScopeRef) -> tuple[tuple[str, str], ...]:
        return run_async_blocking(self.snapshot_async(scope))

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
        return run_async_blocking(
            self.snapshot_many_async(
                scopes,
                exclude_key_prefixes=exclude_key_prefixes,
            )
        )

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
        return run_async_blocking(self.cleanup_expired_async())

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
        run_async_blocking(self.delete_by_scope_key_prefix_async(scope, key_prefix))

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
        run_async_blocking(
            self.delete_by_session_async(
                session_id=session_id,
                task_ids=task_ids,
                instance_ids=instance_ids,
                role_scope_ids=role_scope_ids,
                session_scope_ids=session_scope_ids,
                conversation_ids=conversation_ids,
                workspace_ids=workspace_ids,
            )
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
        run_async_blocking(
            self.delete_for_subagent_async(
                instance_id=instance_id,
                session_scope_id=session_scope_id,
                role_scope_id=role_scope_id,
                conversation_id=conversation_id,
                task_ids=task_ids,
            )
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
