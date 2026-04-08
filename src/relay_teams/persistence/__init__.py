# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.persistence.db import (
    MEMORY_DSN,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_TIMEOUT_SECONDS,
    SQLITE_WRITE_RETRY_ATTEMPTS,
    is_retryable_sqlite_error,
    open_sqlite,
    run_sqlite_write_with_retry,
    sqlite_compile_options,
    sqlite_supports_fts5,
)
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.persistence.shared_state_repo import (
    SharedStateRepository,
    build_global_scope_ref,
)

__all__ = [
    "MEMORY_DSN",
    "SQLITE_BUSY_TIMEOUT_MS",
    "SQLITE_TIMEOUT_SECONDS",
    "SQLITE_WRITE_RETRY_ATTEMPTS",
    "ScopeRef",
    "ScopeType",
    "SharedSqliteRepository",
    "SharedStateRepository",
    "StateMutation",
    "build_global_scope_ref",
    "is_retryable_sqlite_error",
    "open_sqlite",
    "run_sqlite_write_with_retry",
    "sqlite_compile_options",
    "sqlite_supports_fts5",
]
