# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from relay_teams.logger import get_logger
from relay_teams.persistence import (
    SharedSqliteRepository,
    sqlite_supports_fts5,
)
from relay_teams.retrieval.retrieval_models import (
    RetrievalBackendKind,
    RetrievalDocument,
    RetrievalHit,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalStats,
    RetrievalTokenizer,
)
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)
_UNICODE61_SPLIT_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
_MATCH_SANITIZE_PATTERN = re.compile(r'["\'`(){}\[\]:^*]+')


class SqliteFts5RetrievalStore(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path, repository_name="retrieval.sqlite")
        self._require_fts5()
        self._init_tables()

    @property
    def backend_kind(self) -> RetrievalBackendKind:
        return RetrievalBackendKind.SQLITE_FTS5

    def replace_scope(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        normalized_documents = _normalize_documents(documents)
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="replace_scope",
            attributes={
                "scope_kind": config.scope_kind.value,
                "scope_id": config.scope_id,
                "document_count": len(normalized_documents),
                "tokenizer": config.tokenizer.value,
            },
        ):
            self._run_write(
                operation_name="replace_scope",
                operation=lambda: self._replace_scope_locked(
                    config=config,
                    documents=normalized_documents,
                ),
            )
        return self.stats(scope_kind=config.scope_kind, scope_id=config.scope_id)

    async def replace_scope_async(
        self, *, config: RetrievalScopeConfig, documents: tuple[RetrievalDocument, ...]
    ) -> RetrievalStats:
        return await self._call_sync_async(
            self.replace_scope, config=config, documents=documents
        )

    def upsert_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        normalized_documents = _normalize_documents(documents)
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="upsert_documents",
            attributes={
                "scope_kind": config.scope_kind.value,
                "scope_id": config.scope_id,
                "document_count": len(normalized_documents),
                "tokenizer": config.tokenizer.value,
            },
        ):
            self._run_write(
                operation_name="upsert_documents",
                operation=lambda: self._upsert_documents_locked(
                    config=config,
                    documents=normalized_documents,
                ),
            )
        return self.stats(scope_kind=config.scope_kind, scope_id=config.scope_id)

    async def upsert_documents_async(
        self, *, config: RetrievalScopeConfig, documents: tuple[RetrievalDocument, ...]
    ) -> RetrievalStats:
        return await self._call_sync_async(
            self.upsert_documents, config=config, documents=documents
        )

    def delete_documents(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> RetrievalStats:
        normalized_ids = _normalize_document_ids(document_ids)
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="delete_documents",
            attributes={
                "scope_kind": scope_kind.value,
                "scope_id": scope_id,
                "document_count": len(normalized_ids),
            },
        ):
            self._run_write(
                operation_name="delete_documents",
                operation=lambda: self._delete_documents_locked(
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    document_ids=normalized_ids,
                ),
            )
        return self.stats(scope_kind=scope_kind, scope_id=scope_id)

    async def delete_documents_async(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> RetrievalStats:
        return await self._call_sync_async(
            self.delete_documents,
            scope_kind=scope_kind,
            scope_id=scope_id,
            document_ids=document_ids,
        )

    def search(
        self,
        *,
        query: RetrievalQuery,
    ) -> tuple[RetrievalHit, ...]:
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="search",
            attributes={
                "scope_kind": query.scope_kind.value,
                "scope_id": query.scope_id,
                "limit": query.limit,
            },
        ):
            config = self._run_read(
                lambda: self._get_scope_config(
                    scope_kind=query.scope_kind,
                    scope_id=query.scope_id,
                )
            )
            if config is None:
                return ()
            match_expression = _build_match_expression(
                raw_query=query.text,
                tokenizer=config.tokenizer,
            )
            if not match_expression:
                return ()
            table_name = _fts_table_name(config.tokenizer)
            rows = self._run_read(
                lambda: self._conn.execute(
                    f"""
                    SELECT
                        document_id,
                        title,
                        COALESCE(
                            NULLIF(snippet({table_name}, 4, '[', ']', '...', 12), ''),
                            NULLIF(snippet({table_name}, 3, '[', ']', '...', 12), ''),
                            body,
                            title,
                            ''
                        ) AS snippet,
                        bm25({table_name}, 0.0, 0.0, 0.0, ?, ?, ?) AS rank_score
                    FROM {table_name}
                    WHERE scope_kind = ? AND scope_id = ? AND {table_name} MATCH ?
                    ORDER BY rank_score ASC, document_id ASC
                    LIMIT ?
                    """,
                    (
                        config.title_weight,
                        config.body_weight,
                        config.keyword_weight,
                        query.scope_kind.value,
                        query.scope_id,
                        match_expression,
                        query.limit,
                    ),
                ).fetchall()
            )
            return tuple(
                RetrievalHit(
                    document_id=str(row["document_id"]),
                    score=float(-float(row["rank_score"])),
                    rank=index,
                    title=str(row["title"]),
                    snippet=str(row["snippet"]),
                )
                for index, row in enumerate(rows, start=1)
            )

    async def search_async(self, *, query: RetrievalQuery) -> tuple[RetrievalHit, ...]:
        return await self._call_sync_async(self.search, query=query)

    def rebuild_scope(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="rebuild_scope",
            attributes={
                "scope_kind": scope_kind.value,
                "scope_id": scope_id,
            },
        ):
            self._run_write(
                operation_name="rebuild_scope",
                operation=lambda: self._rebuild_scope_locked(
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                ),
            )
        return self.stats(scope_kind=scope_kind, scope_id=scope_id)

    async def rebuild_scope_async(
        self, *, scope_kind: RetrievalScopeKind, scope_id: str
    ) -> RetrievalStats:
        return await self._call_sync_async(
            self.rebuild_scope, scope_kind=scope_kind, scope_id=scope_id
        )

    def stats(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="stats",
            attributes={
                "scope_kind": scope_kind.value,
                "scope_id": scope_id,
            },
        ):
            row = self._run_read(
                lambda: self._conn.execute(
                    """
                    SELECT backend, tokenizer, updated_at
                    FROM retrieval_scopes
                    WHERE scope_kind = ? AND scope_id = ?
                    """,
                    (scope_kind.value, scope_id),
                ).fetchone()
            )
            count_row = self._run_read(
                lambda: self._conn.execute(
                    """
                    SELECT COUNT(*) AS document_count
                    FROM retrieval_documents
                    WHERE scope_kind = ? AND scope_id = ?
                    """,
                    (scope_kind.value, scope_id),
                ).fetchone()
            )
            tokenizer = None
            updated_at = None
            if row is not None:
                tokenizer_value = str(row["tokenizer"])
                tokenizer = RetrievalTokenizer(tokenizer_value)
                updated_at = datetime.fromisoformat(str(row["updated_at"]))
            return RetrievalStats(
                scope_kind=scope_kind,
                scope_id=scope_id,
                backend=self.backend_kind,
                tokenizer=tokenizer,
                document_count=(
                    int(count_row["document_count"]) if count_row is not None else 0
                ),
                updated_at=updated_at,
            )

    async def stats_async(
        self, *, scope_kind: RetrievalScopeKind, scope_id: str
    ) -> RetrievalStats:
        return await self._call_sync_async(
            self.stats, scope_kind=scope_kind, scope_id=scope_id
        )

    def _require_fts5(self) -> None:
        if self._run_read(lambda: sqlite_supports_fts5(self._conn)):
            return
        raise RuntimeError("SQLite FTS5 is required for retrieval indexing")

    def _init_tables(self) -> None:
        with trace_span(
            LOGGER,
            component="retrieval.sqlite",
            operation="init_schema",
            attributes={"backend": self.backend_kind.value},
        ):
            self._run_write(operation_name="init_schema", operation=self._create_tables)

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_scopes (
                scope_kind    TEXT NOT NULL,
                scope_id      TEXT NOT NULL,
                backend       TEXT NOT NULL,
                tokenizer     TEXT NOT NULL,
                title_weight  REAL NOT NULL,
                body_weight   REAL NOT NULL,
                keyword_weight REAL NOT NULL,
                updated_at    TEXT NOT NULL,
                PRIMARY KEY (scope_kind, scope_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_documents (
                rowid         INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_kind    TEXT NOT NULL,
                scope_id      TEXT NOT NULL,
                document_id   TEXT NOT NULL,
                title         TEXT NOT NULL,
                body          TEXT NOT NULL,
                keywords      TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                UNIQUE (scope_kind, scope_id, document_id),
                FOREIGN KEY (scope_kind, scope_id)
                    REFERENCES retrieval_scopes(scope_kind, scope_id)
                    ON DELETE CASCADE
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_retrieval_documents_scope
            ON retrieval_documents(scope_kind, scope_id, updated_at)
            """
        )
        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts_unicode61
            USING fts5(
                scope_kind UNINDEXED,
                scope_id UNINDEXED,
                document_id UNINDEXED,
                title,
                body,
                keywords,
                content='retrieval_documents',
                content_rowid='rowid',
                tokenize='unicode61',
                detail='column'
            )
            """
        )
        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts_trigram
            USING fts5(
                scope_kind UNINDEXED,
                scope_id UNINDEXED,
                document_id UNINDEXED,
                title,
                body,
                keywords,
                content='retrieval_documents',
                content_rowid='rowid',
                tokenize='trigram',
                detail='column'
            )
            """
        )

    def _replace_scope_locked(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> None:
        existing_config = self._get_scope_config(
            scope_kind=config.scope_kind,
            scope_id=config.scope_id,
        )
        existing_rows = self._fetch_scope_rows(
            scope_kind=config.scope_kind,
            scope_id=config.scope_id,
        )
        self._upsert_scope_config(config=config)
        if existing_rows and existing_config is not None:
            self._delete_rows_from_index(
                table_name=_fts_table_name(existing_config.tokenizer),
                rows=existing_rows,
            )
        self._conn.execute(
            """
            DELETE FROM retrieval_documents
            WHERE scope_kind = ? AND scope_id = ?
            """,
            (config.scope_kind.value, config.scope_id),
        )
        if documents:
            self._insert_documents(config=config, documents=documents)
            inserted_rows = self._fetch_scope_rows(
                scope_kind=config.scope_kind,
                scope_id=config.scope_id,
            )
            self._index_rows(tokenizer=config.tokenizer, rows=inserted_rows)

    def _upsert_documents_locked(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> None:
        existing_config = self._get_scope_config(
            scope_kind=config.scope_kind,
            scope_id=config.scope_id,
        )
        previous_scope_rows = (
            self._fetch_scope_rows(
                scope_kind=config.scope_kind,
                scope_id=config.scope_id,
            )
            if existing_config is not None
            and existing_config.tokenizer != config.tokenizer
            else ()
        )
        previous_document_rows = (
            self._fetch_scope_rows(
                scope_kind=config.scope_kind,
                scope_id=config.scope_id,
                document_ids=tuple(document.document_id for document in documents),
            )
            if existing_config is not None
            and existing_config.tokenizer == config.tokenizer
            else ()
        )
        self._upsert_scope_config(config=config)
        self._insert_documents(config=config, documents=documents)
        if (
            existing_config is not None
            and existing_config.tokenizer != config.tokenizer
        ):
            if previous_scope_rows:
                self._delete_rows_from_index(
                    table_name=_fts_table_name(existing_config.tokenizer),
                    rows=previous_scope_rows,
                )
            current_scope_rows = self._fetch_scope_rows(
                scope_kind=config.scope_kind,
                scope_id=config.scope_id,
            )
            self._index_rows(tokenizer=config.tokenizer, rows=current_scope_rows)
            return
        if previous_document_rows:
            self._delete_rows_from_index(
                table_name=_fts_table_name(config.tokenizer),
                rows=previous_document_rows,
            )
        current_rows = self._fetch_scope_rows(
            scope_kind=config.scope_kind,
            scope_id=config.scope_id,
            document_ids=tuple(document.document_id for document in documents),
        )
        self._index_rows(tokenizer=config.tokenizer, rows=current_rows)

    def _delete_documents_locked(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> None:
        if not document_ids:
            return
        existing_rows = self._fetch_scope_rows(
            scope_kind=scope_kind,
            scope_id=scope_id,
            document_ids=document_ids,
        )
        if not existing_rows:
            return
        config = self._get_scope_config(scope_kind=scope_kind, scope_id=scope_id)
        if config is not None:
            self._delete_rows_from_index(
                table_name=_fts_table_name(config.tokenizer),
                rows=existing_rows,
            )
        self._conn.executemany(
            """
            DELETE FROM retrieval_documents
            WHERE scope_kind = ? AND scope_id = ? AND document_id = ?
            """,
            [(scope_kind.value, scope_id, document_id) for document_id in document_ids],
        )

    def _rebuild_scope_locked(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        config: RetrievalScopeConfig | None = None,
    ) -> None:
        resolved_config = config or self._get_scope_config(
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
        if resolved_config is None:
            return
        current_rows = self._fetch_scope_rows(
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
        if current_rows:
            self._delete_rows_from_index(
                table_name=_fts_table_name(resolved_config.tokenizer),
                rows=current_rows,
            )
        self._index_rows(tokenizer=resolved_config.tokenizer, rows=current_rows)
        self._touch_scope(
            scope_kind=scope_kind,
            scope_id=scope_id,
        )

    def _upsert_scope_config(self, *, config: RetrievalScopeConfig) -> None:
        now = _utc_now()
        self._conn.execute(
            """
            INSERT INTO retrieval_scopes(
                scope_kind,
                scope_id,
                backend,
                tokenizer,
                title_weight,
                body_weight,
                keyword_weight,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_kind, scope_id)
            DO UPDATE SET
                backend = excluded.backend,
                tokenizer = excluded.tokenizer,
                title_weight = excluded.title_weight,
                body_weight = excluded.body_weight,
                keyword_weight = excluded.keyword_weight,
                updated_at = excluded.updated_at
            """,
            (
                config.scope_kind.value,
                config.scope_id,
                config.backend.value,
                config.tokenizer.value,
                config.title_weight,
                config.body_weight,
                config.keyword_weight,
                now,
            ),
        )

    def _insert_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> None:
        if not documents:
            return
        now = _utc_now()
        self._conn.executemany(
            """
            INSERT INTO retrieval_documents(
                scope_kind,
                scope_id,
                document_id,
                title,
                body,
                keywords,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_kind, scope_id, document_id)
            DO UPDATE SET
                title = excluded.title,
                body = excluded.body,
                keywords = excluded.keywords,
                updated_at = excluded.updated_at
            """,
            [
                (
                    config.scope_kind.value,
                    config.scope_id,
                    document.document_id,
                    document.title,
                    document.body,
                    " ".join(document.keywords),
                    now,
                )
                for document in documents
            ],
        )
        self._touch_scope(scope_kind=config.scope_kind, scope_id=config.scope_id)

    def _touch_scope(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> None:
        self._conn.execute(
            """
            UPDATE retrieval_scopes
            SET updated_at = ?
            WHERE scope_kind = ? AND scope_id = ?
            """,
            (_utc_now(), scope_kind.value, scope_id),
        )

    def _fetch_scope_rows(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...] | None = None,
    ) -> tuple[sqlite3.Row, ...]:
        if document_ids is None:
            rows = self._conn.execute(
                """
                SELECT rowid, scope_kind, scope_id, document_id, title, body, keywords
                FROM retrieval_documents
                WHERE scope_kind = ? AND scope_id = ?
                ORDER BY document_id ASC
                """,
                (scope_kind.value, scope_id),
            ).fetchall()
            return tuple(rows)
        if not document_ids:
            return ()
        placeholders = ",".join("?" for _ in document_ids)
        rows = self._conn.execute(
            f"""
            SELECT rowid, scope_kind, scope_id, document_id, title, body, keywords
            FROM retrieval_documents
            WHERE scope_kind = ? AND scope_id = ? AND document_id IN ({placeholders})
            ORDER BY document_id ASC
            """,
            (scope_kind.value, scope_id, *document_ids),
        ).fetchall()
        return tuple(rows)

    def _delete_rows_from_index(
        self,
        *,
        table_name: str,
        rows: tuple[sqlite3.Row, ...],
    ) -> None:
        self._conn.executemany(
            f"""
            INSERT INTO {table_name}(
                {table_name},
                rowid,
                scope_kind,
                scope_id,
                document_id,
                title,
                body,
                keywords
            )
            VALUES ('delete', ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(row["rowid"]),
                    str(row["scope_kind"]),
                    str(row["scope_id"]),
                    str(row["document_id"]),
                    str(row["title"]),
                    str(row["body"]),
                    str(row["keywords"]),
                )
                for row in rows
            ],
        )

    def _index_rows(
        self,
        *,
        tokenizer: RetrievalTokenizer,
        rows: tuple[sqlite3.Row, ...],
    ) -> None:
        if not rows:
            return
        table_name = _fts_table_name(tokenizer)
        self._conn.executemany(
            f"""
            INSERT INTO {table_name}(
                rowid,
                scope_kind,
                scope_id,
                document_id,
                title,
                body,
                keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(row["rowid"]),
                    str(row["scope_kind"]),
                    str(row["scope_id"]),
                    str(row["document_id"]),
                    str(row["title"]),
                    str(row["body"]),
                    str(row["keywords"]),
                )
                for row in rows
            ],
        )

    def _get_scope_config(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalScopeConfig | None:
        row = self._conn.execute(
            """
            SELECT
                scope_kind,
                scope_id,
                backend,
                tokenizer,
                title_weight,
                body_weight,
                keyword_weight
            FROM retrieval_scopes
            WHERE scope_kind = ? AND scope_id = ?
            """,
            (scope_kind.value, scope_id),
        ).fetchone()
        if row is None:
            return None
        return RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind(str(row["scope_kind"])),
            scope_id=str(row["scope_id"]),
            backend=RetrievalBackendKind(str(row["backend"])),
            tokenizer=RetrievalTokenizer(str(row["tokenizer"])),
            title_weight=float(row["title_weight"]),
            body_weight=float(row["body_weight"]),
            keyword_weight=float(row["keyword_weight"]),
        )


def _fts_table_name(tokenizer: RetrievalTokenizer) -> str:
    if tokenizer == RetrievalTokenizer.TRIGRAM:
        return "retrieval_fts_trigram"
    return "retrieval_fts_unicode61"


def _normalize_documents(
    documents: tuple[RetrievalDocument, ...],
) -> tuple[RetrievalDocument, ...]:
    deduplicated: dict[str, RetrievalDocument] = {}
    for document in documents:
        deduplicated[document.document_id] = document
    return tuple(deduplicated[key] for key in sorted(deduplicated.keys()))


def _normalize_document_ids(document_ids: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for document_id in document_ids:
        cleaned = document_id.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return tuple(normalized)


def _build_match_expression(
    *,
    raw_query: str,
    tokenizer: RetrievalTokenizer,
) -> str:
    normalized = " ".join(raw_query.strip().split())
    if not normalized:
        return ""
    sanitized = _MATCH_SANITIZE_PATTERN.sub(" ", normalized).strip()
    if not sanitized:
        return ""
    if tokenizer == RetrievalTokenizer.TRIGRAM:
        return _build_trigram_match_expression(sanitized)
    tokens = tuple(
        token.lower()
        for token in _UNICODE61_SPLIT_PATTERN.split(sanitized)
        if token.strip()
    )
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def _build_trigram_match_expression(raw_query: str) -> str:
    segments = tuple(
        token.lower()
        for token in _UNICODE61_SPLIT_PATTERN.split(raw_query)
        if token.strip()
    )
    grams: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        if len(segment) < 3:
            continue
        for index in range(len(segment) - 2):
            gram = segment[index : index + 3]
            if gram in seen:
                continue
            seen.add(gram)
            grams.append(gram)
    if not grams:
        return ""
    return " OR ".join(grams)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
