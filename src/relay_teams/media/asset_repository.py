from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from relay_teams.media.models import (
    MediaAssetRecord,
    MediaAssetStorageKind,
    MediaModality,
)
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository


class MediaAssetRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_assets (
                    asset_id            TEXT PRIMARY KEY,
                    session_id          TEXT NOT NULL,
                    workspace_id        TEXT NOT NULL,
                    storage_kind        TEXT NOT NULL,
                    modality            TEXT NOT NULL,
                    mime_type           TEXT NOT NULL,
                    name                TEXT NOT NULL DEFAULT '',
                    relative_path       TEXT,
                    external_url        TEXT,
                    size_bytes          INTEGER,
                    width               INTEGER,
                    height              INTEGER,
                    duration_ms         INTEGER,
                    thumbnail_asset_id  TEXT,
                    source              TEXT NOT NULL DEFAULT '',
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_assets_session ON media_assets(session_id, created_at ASC)"
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MediaAssetRepository",
            operation_name="init_tables",
        )

    def upsert(self, record: MediaAssetRecord) -> MediaAssetRecord:
        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO media_assets(
                    asset_id,
                    session_id,
                    workspace_id,
                    storage_kind,
                    modality,
                    mime_type,
                    name,
                    relative_path,
                    external_url,
                    size_bytes,
                    width,
                    height,
                    duration_ms,
                    thumbnail_asset_id,
                    source,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id)
                DO UPDATE SET
                    session_id=excluded.session_id,
                    workspace_id=excluded.workspace_id,
                    storage_kind=excluded.storage_kind,
                    modality=excluded.modality,
                    mime_type=excluded.mime_type,
                    name=excluded.name,
                    relative_path=excluded.relative_path,
                    external_url=excluded.external_url,
                    size_bytes=excluded.size_bytes,
                    width=excluded.width,
                    height=excluded.height,
                    duration_ms=excluded.duration_ms,
                    thumbnail_asset_id=excluded.thumbnail_asset_id,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    record.asset_id,
                    record.session_id,
                    record.workspace_id,
                    record.storage_kind.value,
                    record.modality.value,
                    record.mime_type,
                    record.name,
                    record.relative_path,
                    record.external_url,
                    record.size_bytes,
                    record.width,
                    record.height,
                    record.duration_ms,
                    record.thumbnail_asset_id,
                    record.source,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MediaAssetRepository",
            operation_name="upsert",
        )
        return self.get(record.asset_id)

    async def upsert_async(self, record: MediaAssetRecord) -> MediaAssetRecord:
        return await self._call_sync_async(self.upsert, record)

    def get(self, asset_id: str) -> MediaAssetRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM media_assets WHERE asset_id=?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        return self._to_record(row)

    async def get_async(self, asset_id: str) -> MediaAssetRecord:
        return await self._call_sync_async(self.get, asset_id)

    def list_by_session(self, session_id: str) -> tuple[MediaAssetRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM media_assets WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    async def list_by_session_async(
        self, session_id: str
    ) -> tuple[MediaAssetRecord, ...]:
        return await self._call_sync_async(self.list_by_session, session_id)

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM media_assets WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="MediaAssetRepository",
            operation_name="delete_by_session",
        )

    async def delete_by_session_async(self, session_id: str) -> None:
        return await self._call_sync_async(self.delete_by_session, session_id)

    def _to_record(self, row: sqlite3.Row) -> MediaAssetRecord:
        return MediaAssetRecord(
            asset_id=str(row["asset_id"]),
            session_id=str(row["session_id"]),
            workspace_id=str(row["workspace_id"]),
            storage_kind=MediaAssetStorageKind(str(row["storage_kind"])),
            modality=MediaModality(str(row["modality"])),
            mime_type=str(row["mime_type"]),
            name=str(row["name"] or ""),
            relative_path=(
                str(row["relative_path"]) if row["relative_path"] is not None else None
            ),
            external_url=(
                str(row["external_url"]) if row["external_url"] is not None else None
            ),
            size_bytes=int(row["size_bytes"])
            if row["size_bytes"] is not None
            else None,
            width=int(row["width"]) if row["width"] is not None else None,
            height=int(row["height"]) if row["height"] is not None else None,
            duration_ms=(
                int(row["duration_ms"]) if row["duration_ms"] is not None else None
            ),
            thumbnail_asset_id=(
                str(row["thumbnail_asset_id"])
                if row["thumbnail_asset_id"] is not None
                else None
            ),
            source=str(row["source"] or ""),
            created_at=datetime.fromisoformat(str(row["created_at"])).astimezone(
                timezone.utc
            ),
            updated_at=datetime.fromisoformat(str(row["updated_at"])).astimezone(
                timezone.utc
            ),
        )
