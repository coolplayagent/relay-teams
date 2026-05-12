from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import logging
import os
from threading import RLock
from time import monotonic
from typing import Generic, TypeVar, cast

from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.session_read_models import (
    CachedReadResult,
    SessionRoundsQueryKey,
    SessionSnapshotCacheDiagnostics,
    SessionSnapshotSection,
    utc_now,
)

ResultT = TypeVar("ResultT")
LOGGER = get_logger(__name__)
ProjectionRefreshRunner = Callable[
    [str, Callable[[], ResultT]],
    Awaitable[ResultT],
]
SnapshotCacheKey = tuple[str, str, str]
DEFAULT_SESSION_SNAPSHOT_CACHE_MAX_AGE_SECONDS = 0.75
DEFAULT_SESSION_SNAPSHOT_CACHE_MS = 1000
DEFAULT_SESSION_COLD_MISS_TIMEOUT_SECONDS = 2.0
DEFAULT_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS = 500
DEFAULT_SESSION_SNAPSHOT_CACHE_MAX_ENTRIES = 2048
SESSION_SNAPSHOT_CACHE_MS_ENV = "RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MS"
SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS_ENV = (
    "RELAY_TEAMS_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS"
)
SESSION_SNAPSHOT_CACHE_MAX_ENTRIES_ENV = (
    "RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MAX_ENTRIES"
)


def resolve_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        log_event(
            LOGGER,
            logging.WARNING,
            event="session.snapshot_cache.invalid_env",
            message="Ignoring invalid session snapshot cache environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    if value < 1:
        log_event(
            LOGGER,
            logging.WARNING,
            event="session.snapshot_cache.invalid_env",
            message="Ignoring non-positive session snapshot cache environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    return value


def resolve_session_snapshot_cache_max_age_seconds() -> float:
    return (
        resolve_positive_int_env(
            SESSION_SNAPSHOT_CACHE_MS_ENV,
            DEFAULT_SESSION_SNAPSHOT_CACHE_MS,
        )
        / 1000
    )


def resolve_session_snapshot_refresh_min_interval_seconds() -> float:
    return (
        resolve_positive_int_env(
            SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS_ENV,
            DEFAULT_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS,
        )
        / 1000
    )


async def default_projection_refresh_runner(
    operation: str,
    refresh: Callable[[], ResultT],
) -> ResultT:
    _ = operation
    return await asyncio.to_thread(refresh)


class _CacheEntry(Generic[ResultT]):
    def __init__(self) -> None:
        self.value: ResultT | None = None
        self.generated_at: datetime | None = None
        self.generated_monotonic: float | None = None
        self.dirty = True
        self.requires_fresh_read = False
        self.refresh_duration_ms: int | None = None
        self.refresh_error: str | None = None
        self.refresh_task: asyncio.Task[ResultT] | None = None
        self.refresh_started_monotonic: float | None = None
        self.generation = 0
        self.refresh_generation = 0


class StaleFirstCache(Generic[ResultT]):
    def __init__(
        self,
        *,
        operation_name: str,
        refresh_runner: ProjectionRefreshRunner[ResultT] | None = None,
        max_age_seconds: float | None = None,
        cold_miss_timeout_seconds: float = DEFAULT_SESSION_COLD_MISS_TIMEOUT_SECONDS,
        refresh_min_interval_seconds: float | None = None,
    ) -> None:
        self._operation_name = operation_name
        self._refresh_runner = refresh_runner or default_projection_refresh_runner
        self._max_age_seconds = (
            max_age_seconds
            if max_age_seconds is not None
            else resolve_session_snapshot_cache_max_age_seconds()
        )
        self._cold_miss_timeout_seconds = cold_miss_timeout_seconds
        self._refresh_min_interval_seconds = (
            refresh_min_interval_seconds
            if refresh_min_interval_seconds is not None
            else resolve_session_snapshot_refresh_min_interval_seconds()
        )
        self._entry: _CacheEntry[ResultT] = _CacheEntry()
        self._lock = asyncio.Lock()
        self._sync_lock = RLock()
        self._clear_count = 0

    def mark_dirty(self, *, requires_fresh_read: bool = False) -> None:
        with self._sync_lock:
            self._entry.dirty = True
            self._entry.generation += 1
            self._entry.requires_fresh_read = (
                self._entry.requires_fresh_read or requires_fresh_read
            )

    def clear(self) -> None:
        with self._sync_lock:
            previous_entry = self._entry
            next_entry: _CacheEntry[ResultT] = _CacheEntry()
            next_entry.generation = previous_entry.generation + 1
            self._entry = next_entry
            self._clear_count += 1

    def update_value(self, update: Callable[[ResultT], ResultT]) -> bool:
        with self._sync_lock:
            value = self._entry.value
            if value is None:
                self._entry.dirty = True
                self._entry.generation += 1
                return False
            self._entry.generation += 1
            self._entry.value = update(value)
            self._entry.generated_at = utc_now()
            self._entry.generated_monotonic = monotonic()
            return True

    async def read(
        self,
        refresh: Callable[[], ResultT],
        *,
        force_refresh: bool = False,
        fallback: Callable[[], ResultT] | None = None,
        failure_fallback: Callable[[], ResultT] | None = None,
    ) -> CachedReadResult[ResultT]:
        with self._sync_lock:
            clear_count = self._clear_count
        initial_awaitable = await self._prepare_read(
            refresh,
            force_refresh=force_refresh,
        )
        if initial_awaitable is not None:
            initial_fallback = await self._wait_for_refresh_or_fallback(
                initial_awaitable,
                force_refresh=force_refresh,
                fallback=fallback,
                failure_fallback=failure_fallback,
            )
            if initial_fallback is not None:
                return initial_fallback
        post_lock_awaitable: asyncio.Future[ResultT] | None = None
        async with self._lock:
            with self._sync_lock:
                value = self._entry.value
                if value is None:
                    if self._clear_count != clear_count:
                        raise RuntimeError(
                            "Session snapshot cache was cleared during read"
                        )
                    post_lock_awaitable = self._ensure_background_refresh_locked(
                        refresh
                    )
        if post_lock_awaitable is not None:
            post_lock_fallback = await self._wait_for_refresh_or_fallback(
                post_lock_awaitable,
                force_refresh=force_refresh,
                fallback=fallback,
                failure_fallback=failure_fallback,
            )
            if post_lock_fallback is not None:
                return post_lock_fallback
        async with self._lock:
            with self._sync_lock:
                value = self._entry.value
                if value is None:
                    raise RuntimeError(
                        "Session snapshot refresh finished without a value"
                    )
                return CachedReadResult(
                    value=value,
                    diagnostics=self._diagnostics_locked(
                        cache_hit=(
                            initial_awaitable is None and post_lock_awaitable is None
                        ),
                    ),
                )

    async def _wait_for_refresh_or_fallback(
        self,
        refresh_task: asyncio.Future[ResultT],
        *,
        force_refresh: bool,
        fallback: Callable[[], ResultT] | None,
        failure_fallback: Callable[[], ResultT] | None,
    ) -> CachedReadResult[ResultT] | None:
        if force_refresh:
            return await self._wait_for_refresh_completion(refresh_task)
        try:
            await asyncio.wait_for(
                asyncio.shield(refresh_task),
                timeout=self._cold_miss_timeout_seconds,
            )
        except TimeoutError:
            stale_result = await self._stale_timeout_result()
            if stale_result is not None:
                return stale_result
            if fallback is not None:
                return CachedReadResult(
                    value=fallback(),
                    diagnostics=await self._diagnostics_async(
                        cache_hit=False,
                        fallback=True,
                    ),
                )
            return await self._wait_for_refresh_completion(refresh_task)
        except Exception:
            stale_result = await self._stale_timeout_result()
            if stale_result is not None:
                return stale_result
            if failure_fallback is not None:
                return CachedReadResult(
                    value=failure_fallback(),
                    diagnostics=await self._diagnostics_async(
                        cache_hit=False,
                        fallback=True,
                    ),
                )
            raise
        return None

    async def _wait_for_refresh_completion(
        self,
        refresh_task: asyncio.Future[ResultT],
    ) -> CachedReadResult[ResultT]:
        refresh_result = await refresh_task
        async with self._lock:
            with self._sync_lock:
                value = self._entry.value
                if value is None:
                    value = refresh_result
                elif self._entry.dirty or self._entry.requires_fresh_read:
                    value = refresh_result
                if value is None:
                    raise RuntimeError(
                        "Session snapshot refresh finished without a cached value "
                        f"after {type(refresh_result).__name__} result"
                    )
                return CachedReadResult(
                    value=value,
                    diagnostics=self._diagnostics_locked(cache_hit=False),
                )

    async def _stale_timeout_result(self) -> CachedReadResult[ResultT] | None:
        async with self._lock:
            with self._sync_lock:
                value = self._entry.value
                if value is None:
                    return None
                return CachedReadResult(
                    value=value,
                    diagnostics=self._diagnostics_locked(cache_hit=True),
                )

    async def _prepare_read(
        self,
        refresh: Callable[[], ResultT],
        *,
        force_refresh: bool,
    ) -> asyncio.Future[ResultT] | None:
        async with self._lock:
            with self._sync_lock:
                has_value = self._entry.value is not None
                is_stale = (
                    self._entry.dirty
                    or self._entry.requires_fresh_read
                    or self._is_expired_locked()
                )
                requires_fresh_read = self._entry.requires_fresh_read
                if not force_refresh and has_value and not is_stale:
                    return None
                if not force_refresh and has_value and is_stale:
                    task = self._ensure_background_refresh_locked(
                        refresh,
                        force=requires_fresh_read,
                    )
                    if requires_fresh_read:
                        return task
                    return None
                task = self._ensure_background_refresh_locked(
                    refresh,
                    force=force_refresh,
                )
                return task

    def _ensure_background_refresh_locked(
        self,
        refresh: Callable[[], ResultT],
        *,
        force: bool = False,
    ) -> asyncio.Future[ResultT]:
        existing = self._entry.refresh_task
        current_generation = self._entry.generation
        if (
            existing is not None
            and not existing.done()
            and self._entry.refresh_generation >= current_generation
        ):
            return existing
        if (
            not force
            and self._entry.value is not None
            and self._entry.refresh_started_monotonic is not None
            and monotonic() - self._entry.refresh_started_monotonic
            < self._refresh_min_interval_seconds
        ):
            completed = asyncio.get_running_loop().create_future()
            value = self._entry.value
            if value is None:
                raise RuntimeError("Session snapshot refresh finished without a value")
            completed.set_result(value)
            return completed
        self._entry.refresh_started_monotonic = monotonic()
        self._entry.refresh_generation = current_generation
        task = asyncio.create_task(
            self._refresh_task(refresh, current_generation, self._clear_count)
        )
        task.add_done_callback(self._observe_refresh_task)
        self._entry.refresh_task = task
        return task

    @staticmethod
    def _observe_refresh_task(task: asyncio.Task[ResultT]) -> None:
        try:
            exception = task.exception()
        except asyncio.CancelledError:
            return
        if exception is None:
            return
        log_event(
            LOGGER,
            logging.DEBUG,
            event="session.snapshot_cache.background_refresh_failed",
            message="Session snapshot background refresh failed",
            payload={"error": f"{type(exception).__name__}: {exception}"},
        )

    async def _refresh_task(
        self,
        refresh: Callable[[], ResultT],
        generation: int,
        clear_count: int,
    ) -> ResultT:
        started = monotonic()
        try:
            value = await self._refresh_runner(self._operation_name, refresh)
        except Exception as exc:
            async with self._lock:
                with self._sync_lock:
                    if generation < self._entry.generation:
                        value = self._entry.value
                        if value is None:
                            raise RuntimeError(
                                "Session snapshot refresh finished without a value"
                            )
                        return value
                    self._entry.refresh_duration_ms = int(
                        (monotonic() - started) * 1000
                    )
                    self._entry.refresh_error = f"{type(exc).__name__}: {exc}"
                    self._entry.dirty = True
                    self._entry.refresh_task = None
            raise
        async with self._lock:
            with self._sync_lock:
                if generation < self._entry.generation:
                    if self._entry.value is None and clear_count == self._clear_count:
                        self._entry.value = value
                        self._entry.generated_at = utc_now()
                        self._entry.generated_monotonic = monotonic()
                        self._entry.refresh_duration_ms = int(
                            (monotonic() - started) * 1000
                        )
                        self._entry.refresh_error = None
                        self._entry.refresh_task = None
                    return value
                self._entry.value = value
                self._entry.generated_at = utc_now()
                self._entry.generated_monotonic = monotonic()
                self._entry.refresh_duration_ms = int((monotonic() - started) * 1000)
                self._entry.refresh_error = None
                self._entry.dirty = False
                self._entry.requires_fresh_read = False
                self._entry.refresh_task = None
        return value

    async def _diagnostics_async(
        self,
        *,
        cache_hit: bool,
        fallback: bool = False,
    ) -> SessionSnapshotCacheDiagnostics:
        async with self._lock:
            with self._sync_lock:
                return self._diagnostics_locked(
                    cache_hit=cache_hit,
                    fallback=fallback,
                )

    def _diagnostics_locked(
        self,
        *,
        cache_hit: bool,
        fallback: bool = False,
    ) -> SessionSnapshotCacheDiagnostics:
        with self._sync_lock:
            age_ms = self._snapshot_age_ms_locked()
            refresh_task = self._entry.refresh_task
            refresh_in_progress = refresh_task is not None and not refresh_task.done()
            dirty = (
                self._entry.dirty
                or self._entry.requires_fresh_read
                or self._is_expired_locked()
            )
            return SessionSnapshotCacheDiagnostics(
                cache_hit=cache_hit,
                stale=(cache_hit and dirty) or fallback,
                dirty=dirty,
                snapshot_age_ms=age_ms,
                refresh_duration_ms=self._entry.refresh_duration_ms,
                refresh_in_progress=refresh_in_progress,
                generated_at=self._entry.generated_at,
                refresh_error=self._entry.refresh_error,
            )

    def _snapshot_age_ms_locked(self) -> int | None:
        if self._entry.generated_monotonic is None:
            return None
        return max(0, int((monotonic() - self._entry.generated_monotonic) * 1000))

    def _is_expired_locked(self) -> bool:
        if self._entry.generated_monotonic is None:
            return True
        return monotonic() - self._entry.generated_monotonic > self._max_age_seconds


class SessionSnapshotCache:
    def __init__(
        self,
        *,
        refresh_runner: ProjectionRefreshRunner[object] | None = None,
        max_age_seconds: float | None = None,
        cold_miss_timeout_seconds: float = DEFAULT_SESSION_COLD_MISS_TIMEOUT_SECONDS,
        refresh_min_interval_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> None:
        self._refresh_runner = refresh_runner
        self._max_age_seconds = max_age_seconds
        self._cold_miss_timeout_seconds = cold_miss_timeout_seconds
        self._refresh_min_interval_seconds = refresh_min_interval_seconds
        self._max_entries = (
            max_entries
            if max_entries is not None
            else resolve_positive_int_env(
                SESSION_SNAPSHOT_CACHE_MAX_ENTRIES_ENV,
                DEFAULT_SESSION_SNAPSHOT_CACHE_MAX_ENTRIES,
            )
        )
        self._entries: dict[SnapshotCacheKey, object] = {}
        self._entries_lock = RLock()

    def mark_session_dirty(
        self,
        session_id: str,
        *,
        requires_fresh_read: bool = False,
    ) -> None:
        safe_session_id = str(session_id or "").strip()
        if not safe_session_id:
            return
        with self._entries_lock:
            matching_caches = [
                cache
                for key, cache in self._entries.items()
                if key[0] == safe_session_id and isinstance(cache, StaleFirstCache)
            ]
        for cache in matching_caches:
            cache.mark_dirty(requires_fresh_read=requires_fresh_read)

    def clear_session(self, session_id: str) -> None:
        safe_session_id = str(session_id or "").strip()
        with self._entries_lock:
            keys = [key for key in self._entries if key[0] == safe_session_id]
            caches = [
                cache
                for key in keys
                if isinstance(cache := self._entries.pop(key), StaleFirstCache)
            ]
        for cache in caches:
            cache.clear()

    def mark_all_dirty(self) -> None:
        with self._entries_lock:
            caches = tuple(
                cache
                for cache in self._entries.values()
                if isinstance(cache, StaleFirstCache)
            )
        for cache in caches:
            cache.mark_dirty()

    async def read(
        self,
        *,
        session_id: str,
        section: SessionSnapshotSection,
        refresh: Callable[[], ResultT],
        force_refresh: bool = False,
        fallback: Callable[[], ResultT] | None = None,
        failure_fallback: Callable[[], ResultT] | None = None,
        rounds_key: SessionRoundsQueryKey | None = None,
    ) -> CachedReadResult[ResultT]:
        cache_key = self._cache_key(
            session_id=session_id,
            section=section,
            rounds_key=rounds_key,
        )
        with self._entries_lock:
            cache = self._entries.get(cache_key)
            if cache is None:
                operation = self._operation_name(
                    session_id=session_id,
                    section=section,
                    rounds_key=rounds_key,
                )
                raw_cache = StaleFirstCache[object](
                    operation_name=operation,
                    refresh_runner=self._refresh_runner,
                    max_age_seconds=self._max_age_seconds,
                    cold_miss_timeout_seconds=self._cold_miss_timeout_seconds,
                    refresh_min_interval_seconds=self._refresh_min_interval_seconds,
                )
                self._entries[cache_key] = raw_cache
                self._prune_entries_locked(protected_key=cache_key)
                # noinspection PyUnnecessaryCast
                cache = cast(StaleFirstCache[ResultT], raw_cache)
            else:
                cache = cast(StaleFirstCache[ResultT], cache)
        return await cache.read(
            refresh,
            force_refresh=force_refresh,
            fallback=fallback,
            failure_fallback=failure_fallback or fallback,
        )

    def _prune_entries_locked(self, *, protected_key: SnapshotCacheKey) -> None:
        while len(self._entries) > self._max_entries:
            oldest_key = next(
                (key for key in self._entries if key != protected_key),
                None,
            )
            if oldest_key is None:
                return
            stale_cache = self._entries.pop(oldest_key)
            if isinstance(stale_cache, StaleFirstCache):
                stale_cache.clear()

    @staticmethod
    def _cache_key(
        *,
        session_id: str,
        section: SessionSnapshotSection,
        rounds_key: SessionRoundsQueryKey | None,
    ) -> SnapshotCacheKey:
        key = rounds_key.cache_key() if rounds_key is not None else ""
        return str(session_id or "").strip(), section.value, key

    @staticmethod
    def _operation_name(
        *,
        session_id: str,
        section: SessionSnapshotSection,
        rounds_key: SessionRoundsQueryKey | None,
    ) -> str:
        suffix = f":{rounds_key.cache_key()}" if rounds_key is not None else ""
        return f"session_snapshot.{section.value}:{session_id}{suffix}"
