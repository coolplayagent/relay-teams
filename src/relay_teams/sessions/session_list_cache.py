from __future__ import annotations

from collections.abc import Callable

from relay_teams.sessions.session_models import SessionRecord
from relay_teams.sessions.session_read_models import CachedReadResult
from relay_teams.sessions.session_snapshot_cache import (
    DEFAULT_SESSION_COLD_MISS_TIMEOUT_SECONDS,
)
from relay_teams.sessions.session_snapshot_cache import ProjectionRefreshRunner
from relay_teams.sessions.session_snapshot_cache import StaleFirstCache
from relay_teams.sessions.session_snapshot_cache import resolve_positive_int_env

LIST_SESSIONS_CACHE_MS_ENV = "RELAY_TEAMS_LIST_SESSIONS_CACHE_MS"
DEFAULT_LIST_SESSIONS_CACHE_MS = 1000


class SessionListCache:
    def __init__(
        self,
        *,
        refresh_runner: ProjectionRefreshRunner[object] | None = None,
        max_age_seconds: float | None = None,
        cold_miss_timeout_seconds: float = DEFAULT_SESSION_COLD_MISS_TIMEOUT_SECONDS,
    ) -> None:
        typed_refresh_runner: ProjectionRefreshRunner[tuple[SessionRecord, ...]] | None
        typed_refresh_runner = None
        if refresh_runner is not None:

            async def run_session_list_refresh(
                operation: str,
                refresh: Callable[[], tuple[SessionRecord, ...]],
            ) -> tuple[SessionRecord, ...]:
                result = await refresh_runner(operation, refresh)
                if not isinstance(result, tuple):
                    raise TypeError("Session list refresh returned an invalid result")
                return result

            typed_refresh_runner = run_session_list_refresh
        self._cache = StaleFirstCache[tuple[SessionRecord, ...]](
            operation_name="session_list",
            refresh_runner=typed_refresh_runner,
            max_age_seconds=(
                max_age_seconds
                if max_age_seconds is not None
                else resolve_positive_int_env(
                    LIST_SESSIONS_CACHE_MS_ENV,
                    DEFAULT_LIST_SESSIONS_CACHE_MS,
                )
                / 1000
            ),
            cold_miss_timeout_seconds=cold_miss_timeout_seconds,
        )

    def mark_dirty(self, *, requires_fresh_read: bool = False) -> None:
        self._cache.mark_dirty(requires_fresh_read=requires_fresh_read)

    def clear(self) -> None:
        self._cache.clear()

    def merge_record(self, record: SessionRecord) -> None:
        merged = self._cache.update_value(
            lambda records: _merge_record(records, record)
        )
        if not merged:
            self.mark_dirty()

    def remove_record(self, session_id: str) -> None:
        removed = self._cache.update_value(
            lambda records: tuple(
                record for record in records if record.session_id != session_id
            )
        )
        if not removed:
            self.mark_dirty()

    async def read(
        self,
        refresh: Callable[[], tuple[SessionRecord, ...]],
        *,
        force_refresh: bool = False,
    ) -> CachedReadResult[tuple[SessionRecord, ...]]:
        return await self._cache.read(
            refresh,
            force_refresh=force_refresh,
        )


def _merge_record(
    records: tuple[SessionRecord, ...],
    record: SessionRecord,
) -> tuple[SessionRecord, ...]:
    merged: list[SessionRecord] = []
    replaced = False
    for cached in records:
        if cached.session_id == record.session_id:
            merged.append(record)
            replaced = True
        else:
            merged.append(cached)
    if not replaced:
        return record, *records
    return tuple(merged)
