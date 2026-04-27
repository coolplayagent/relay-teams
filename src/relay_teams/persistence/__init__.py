# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.persistence.db import (
    MEMORY_DSN,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_TIMEOUT_SECONDS,
    SQLITE_WRITE_RETRY_ATTEMPTS,
    async_sqlite_compile_options,
    async_sqlite_supports_fts5,
    is_retryable_sqlite_error,
    open_async_sqlite,
    run_async_blocking,
    run_async_sqlite_write_with_retry,
    run_sqlite_write_with_retry,
    sqlite_compile_options,
    sqlite_supports_fts5,
)
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.sqlite_repository import (
    BlockingAsyncSqliteConnection,
    BlockingAsyncSqliteCursor,
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
    close_live_sqlite_repositories_async,
)
from relay_teams.persistence.shared_state_repo import (
    SharedStateRepository,
    build_global_scope_ref,
)

__all__ = [
    "MEMORY_DSN",
    "SQLITE_BUSY_TIMEOUT_MS",
    "SQLITE_TIMEOUT_SECONDS",
    "SQLITE_WRITE_RETRY_ATTEMPTS",
    "BlockingAsyncSqliteConnection",
    "BlockingAsyncSqliteCursor",
    "ScopeRef",
    "ScopeType",
    "SharedSqliteRepository",
    "SharedStateRepository",
    "StateMutation",
    "async_fetchall",
    "async_fetchone",
    "async_sqlite_compile_options",
    "async_sqlite_supports_fts5",
    "build_global_scope_ref",
    "close_live_sqlite_repositories_async",
    "is_retryable_sqlite_error",
    "open_async_sqlite",
    "run_async_blocking",
    "run_async_sqlite_write_with_retry",
    "run_sqlite_write_with_retry",
    "sqlite_compile_options",
    "sqlite_supports_fts5",
]
