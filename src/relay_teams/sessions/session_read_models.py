from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

ResultT = TypeVar("ResultT")


class SessionSnapshotSection(str, Enum):
    RECOVERY = "recovery"
    ROUNDS = "rounds"
    AGENTS = "agents"
    SUBAGENTS = "subagents"
    TASKS = "tasks"
    TOKEN_USAGE = "token_usage"


class SessionRoundsQueryKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = 8
    cursor_run_id: str | None = None
    timeline: bool = False
    summary: bool = False

    def cache_key(self) -> str:
        cursor = self.cursor_run_id or ""
        return (
            f"limit={self.limit};cursor={cursor};"
            f"timeline={self.timeline};summary={self.summary}"
        )


class SessionSnapshotCacheDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_hit: bool
    stale: bool
    dirty: bool
    snapshot_age_ms: int | None = None
    refresh_duration_ms: int | None = None
    refresh_in_progress: bool = False
    generated_at: datetime | None = None
    refresh_error: str | None = None


class CachedReadResult(Generic[ResultT]):
    def __init__(
        self,
        *,
        value: ResultT,
        diagnostics: SessionSnapshotCacheDiagnostics,
    ) -> None:
        self.value = value
        self.diagnostics = diagnostics


class SessionSubagentsSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    items: list[dict[str, object]] = Field(default_factory=list)
    cache: SessionSnapshotCacheDiagnostics


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
