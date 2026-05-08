from __future__ import annotations

import asyncio
from threading import Event, Lock
from time import monotonic
from typing import cast

import pytest

from relay_teams.sessions import session_list_cache as session_list_cache_module
from relay_teams.sessions import session_read_models as session_read_models_module
from relay_teams.sessions.session_models import SessionRecord
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.session_service import (
    SessionService,
    _RecoverySnapshotCacheEntry,
)
from relay_teams.providers.token_usage_repo import SessionTokenUsage


class _SessionRepo:
    def __init__(self) -> None:
        self.get_count = 0

    def get(self, session_id: str) -> SessionRecord:
        self.get_count += 1
        return SessionRecord(session_id=session_id, workspace_id="workspace-1")


def _snapshot_service() -> SessionService:
    service = cast(SessionService, object.__new__(SessionService))
    service._recovery_cache_ms = 500
    service._snapshot_refresh_min_interval_ms = 250
    service._rounds_snapshot_cache = {}
    service._rounds_snapshot_cache_lock = Lock()
    service._rounds_refresh_tasks = {}
    service._rounds_snapshot_args_by_key = {}
    service._subagents_snapshot_cache = {}
    service._subagents_snapshot_cache_lock = Lock()
    service._subagents_refresh_tasks = {}
    service._agents_snapshot_cache = {}
    service._agents_snapshot_cache_lock = Lock()
    service._agents_refresh_tasks = {}
    service._tasks_snapshot_cache = {}
    service._tasks_snapshot_cache_lock = Lock()
    service._tasks_refresh_tasks = {}
    service._token_usage_snapshot_cache = {}
    service._token_usage_snapshot_cache_lock = Lock()
    service._token_usage_refresh_tasks = {}
    service._recovery_snapshot_cache = {}
    service._recovery_snapshot_cache_lock = Lock()
    service._recovery_refresh_tasks = {}
    service._session_repo = cast(SessionRepository, _SessionRepo())
    return service


def _run_event(event_type: RunEventType) -> RunEvent:
    return RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        event_type=event_type,
    )


@pytest.mark.asyncio
async def test_list_sessions_async_stale_hit_returns_cached_records() -> None:
    service = cast(SessionService, object.__new__(SessionService))
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = True
    service._list_sessions_cache_ttl_seconds = 0.5
    service._list_sessions_refresh_task = None
    service._list_sessions_refresh_started_monotonic = 0.0
    cached_record = SessionRecord(session_id="session-1", workspace_id="workspace-1")
    service._list_sessions_cache = (monotonic() - 10, (cached_record,))
    refresh_count = 0
    refresh_force_values: list[bool] = []

    def ensure_refresh_task(*, force: bool = False) -> None:
        nonlocal refresh_count
        refresh_count += 1
        refresh_force_values.append(force)

    service._ensure_list_sessions_refresh_task = ensure_refresh_task

    records = await service.list_sessions_async()

    assert records == (cached_record,)
    assert refresh_count == 1
    assert refresh_force_values == [True]


def test_merge_session_record_does_not_seed_cold_list_cache_after_create() -> None:
    service = cast(SessionService, object.__new__(SessionService))
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = False
    service._list_sessions_cache = None
    record = SessionRecord(session_id="session-new", workspace_id="workspace-1")

    service._merge_record_into_list_sessions_cache(record)

    assert service._list_sessions_cache is None
    assert service._list_sessions_cache_dirty is True


def test_merge_session_record_updates_existing_list_cache() -> None:
    service = cast(SessionService, object.__new__(SessionService))
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = False
    existing = SessionRecord(session_id="session-old", workspace_id="workspace-1")
    record = SessionRecord(session_id="session-new", workspace_id="workspace-1")
    service._list_sessions_cache = (monotonic(), (existing,))

    service._merge_record_into_list_sessions_cache(record)

    cached = service._list_sessions_cache
    assert cached is not None
    assert cached[1] == (record, existing)
    assert service._list_sessions_cache_dirty is False


def test_list_sessions_cache_env_resolver_ignores_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CACHE_MS", "bad")
    assert session_list_cache_module.resolve_positive_int_env("CACHE_MS", 500) == 500
    monkeypatch.setenv("CACHE_MS", "0")
    assert session_list_cache_module.resolve_positive_int_env("CACHE_MS", 500) == 500
    monkeypatch.setenv("CACHE_MS", "250")
    assert session_list_cache_module.resolve_positive_int_env("CACHE_MS", 500) == 250
    monkeypatch.delenv("CACHE_MS")
    assert session_list_cache_module.resolve_positive_int_env("CACHE_MS", 500) == 500


def test_list_sessions_cache_get_respects_stale_policy() -> None:
    service = cast(SessionService, object.__new__(SessionService))
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = True
    service._list_sessions_cache_ttl_seconds = 0.5
    cached_record = SessionRecord(session_id="session-1", workspace_id="workspace-1")
    service._list_sessions_cache = (monotonic() - 10, (cached_record,))

    assert service._get_session_from_list_cache("session-1", allow_stale=False) is None
    assert (
        service._get_session_from_list_cache("session-1", allow_stale=True)
        == cached_record
    )
    assert service._get_session_from_list_cache("missing", allow_stale=True) is None


@pytest.mark.asyncio
async def test_list_sessions_async_cold_miss_timeout_returns_empty_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = cast(SessionService, object.__new__(SessionService))
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = False
    service._list_sessions_cache = None
    service._list_sessions_refresh_task = None
    refresh_started = 0

    def slow_list_sessions() -> tuple[SessionRecord, ...]:
        Event().wait(timeout=0.1)
        return ()

    def ensure_refresh_task(*, force: bool = False) -> None:
        nonlocal refresh_started
        _ = force
        refresh_started += 1

    monkeypatch.setattr(
        session_list_cache_module,
        "LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS",
        0.01,
    )
    service.list_sessions = slow_list_sessions
    service._ensure_list_sessions_refresh_task = ensure_refresh_task

    records = await service.list_sessions_async()

    assert records == ()
    assert refresh_started == 1


@pytest.mark.asyncio
async def test_list_sessions_refresh_observer_clears_task_and_retries_dirty_cache() -> (
    None
):
    service = cast(SessionService, object.__new__(SessionService))
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = True
    refresh_force_values: list[bool] = []

    async def fail_refresh() -> None:
        raise RuntimeError("refresh failed")

    def ensure_refresh_task(*, force: bool = False) -> None:
        refresh_force_values.append(force)

    task = asyncio.create_task(fail_refresh())
    service._list_sessions_refresh_task = task
    service._ensure_list_sessions_refresh_task = ensure_refresh_task

    await asyncio.gather(task, return_exceptions=True)
    service._observe_list_sessions_refresh_result(task)

    assert service._list_sessions_refresh_task is None
    assert refresh_force_values == [True]


def test_seed_empty_session_snapshot_caches_primes_switch_read_models() -> None:
    service = _snapshot_service()

    service._seed_empty_session_snapshot_caches("session-1")

    recovery_snapshot = service.get_fast_cached_recovery_snapshot("session-1")
    agents_snapshot = service.get_fast_cached_agents_snapshot("session-1")
    subagents_snapshot = service.get_fast_cached_normal_mode_subagents_snapshot(
        "session-1"
    )
    assert recovery_snapshot is not None
    assert agents_snapshot is not None
    assert subagents_snapshot is not None
    assert agents_snapshot["items"] == []
    assert subagents_snapshot["items"] == []
    assert service._tasks_snapshot_cache["tasks|session-1"].snapshot["items"] == []
    assert (
        service._token_usage_snapshot_cache["token_usage|session-1"].snapshot[
            "total_tokens"
        ]
        == 0
    )
    assert service._rounds_snapshot_cache["session-1|4||0|1"].snapshot["items"] == []
    assert service._rounds_snapshot_args_by_key["session-1|4||0|1"] == (
        "session-1",
        4,
        None,
        False,
        True,
    )
    assert service._rounds_snapshot_args_by_key["session-1|8||0|0"] == (
        "session-1",
        8,
        None,
        False,
        False,
    )


@pytest.mark.asyncio
async def test_clear_session_snapshot_caches_removes_deleted_session_entries() -> None:
    service = _snapshot_service()
    service._seed_empty_session_snapshot_caches("session-1")
    service._seed_empty_session_snapshot_caches("session-2")
    never_complete = asyncio.Event()

    async def wait_forever() -> None:
        await never_complete.wait()

    recovery_task = asyncio.create_task(wait_forever())
    rounds_task = asyncio.create_task(wait_forever())
    service._recovery_refresh_tasks["session-1"] = recovery_task
    service._rounds_refresh_tasks["session-1|8||0|0"] = rounds_task

    service._clear_session_snapshot_caches("session-1")
    await asyncio.sleep(0)
    _ = await asyncio.gather(recovery_task, rounds_task, return_exceptions=True)

    assert service.get_fast_cached_recovery_snapshot("session-1") is None
    assert service.get_fast_cached_recovery_snapshot("session-2") is not None
    assert service._subagents_snapshot_key("session-1") not in (
        service._subagents_snapshot_cache
    )
    assert (
        service._agents_snapshot_key("session-1") not in service._agents_snapshot_cache
    )
    assert service._tasks_snapshot_key("session-1") not in service._tasks_snapshot_cache
    assert (
        service._token_usage_snapshot_key("session-1")
        not in service._token_usage_snapshot_cache
    )
    assert all(
        not key.startswith("session-1|") for key in service._rounds_snapshot_cache
    )
    assert all(
        not key.startswith("session-1|") for key in service._rounds_snapshot_args_by_key
    )
    assert "session-1" not in service._recovery_refresh_tasks
    assert "session-1|8||0|0" not in service._rounds_refresh_tasks
    assert recovery_task.cancelled()
    assert rounds_task.cancelled()


def test_stream_delta_event_does_not_dirty_session_list_cache() -> None:
    service = _snapshot_service()
    service._list_sessions_cache_dirty = False
    refresh_count = 0

    def ensure_list_sessions_refresh_task(*, force: bool = False) -> None:
        nonlocal refresh_count
        _ = force
        refresh_count += 1

    service._ensure_list_sessions_refresh_task = ensure_list_sessions_refresh_task

    service._observe_run_event_for_snapshot_dirty(_run_event(RunEventType.TEXT_DELTA))

    assert service._list_sessions_cache_dirty is False
    assert refresh_count == 0


def test_snapshot_cache_env_resolver_and_key_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _snapshot_service()

    monkeypatch.setenv("RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MS", "750")
    assert service._resolve_session_snapshot_cache_ms() == 750
    monkeypatch.delenv("RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MS")
    monkeypatch.setenv("RELAY_TEAMS_SESSION_RECOVERY_CACHE_MS", "650")
    assert service._resolve_session_snapshot_cache_ms() == 650
    monkeypatch.setenv("RELAY_TEAMS_SESSION_RECOVERY_CACHE_MS", "bad")
    assert service._resolve_session_snapshot_cache_ms() == 1000

    assert service._subagents_snapshot_key("session-1") == "subagents|session-1"
    assert service._agents_snapshot_key("session-1") == "agents|session-1"
    assert service._tasks_snapshot_key("session-1") == "tasks|session-1"
    assert service._token_usage_snapshot_key("session-1") == "token_usage|session-1"


def test_mark_dirty_and_schedule_refresh_cover_all_snapshot_kinds() -> None:
    service = _snapshot_service()
    service._seed_empty_session_snapshot_caches("session-1")
    service._rounds_snapshot_cache["session-1|custom||0|0"] = (
        _RecoverySnapshotCacheEntry(
            snapshot={"items": []}, updated_monotonic=monotonic()
        )
    )
    service._rounds_snapshot_args_by_key["session-1|custom||0|0"] = (
        "session-1",
        4,
        None,
        False,
        False,
    )
    called: list[tuple[str, str]] = []
    service._ensure_recovery_refresh_task = lambda session_id: called.append(
        ("recovery", session_id)
    )
    service._ensure_subagents_refresh_task = lambda session_id: called.append(
        ("subagents", session_id)
    )
    service._ensure_agents_refresh_task = lambda session_id: called.append(
        ("agents", session_id)
    )
    service._ensure_tasks_refresh_task = lambda session_id: called.append(
        ("tasks", session_id)
    )
    service._ensure_token_usage_refresh_task = lambda session_id: called.append(
        ("token_usage", session_id)
    )
    service._ensure_rounds_refresh_task = lambda cache_key: called.append(
        ("rounds", cache_key)
    )

    service._mark_session_snapshot_cache_dirty(
        "session-1",
        requires_fresh_read=True,
    )
    service._schedule_dirty_session_snapshot_refresh("session-1")

    assert service._recovery_snapshot_cache["session-1"].requires_fresh_read is True
    assert (
        service._subagents_snapshot_cache["subagents|session-1"].requires_fresh_read
        is True
    )
    assert (
        service._agents_snapshot_cache["agents|session-1"].requires_fresh_read is True
    )
    assert service._tasks_snapshot_cache["tasks|session-1"].requires_fresh_read is True
    assert (
        service._token_usage_snapshot_cache["token_usage|session-1"].requires_fresh_read
        is True
    )
    assert ("recovery", "session-1") in called
    assert ("subagents", "session-1") in called
    assert ("agents", "session-1") in called
    assert ("tasks", "session-1") in called
    assert ("token_usage", "session-1") in called
    assert ("rounds", "session-1|custom||0|0") in called


@pytest.mark.asyncio
async def test_run_started_event_dirties_session_list_cache_once() -> None:
    service = _snapshot_service()
    service._list_sessions_cache_lock = Lock()
    service._list_sessions_cache_dirty = False
    service._list_sessions_cache_version = 0
    refresh_force_values: list[bool] = []

    def ensure_list_sessions_refresh_task(*, force: bool = False) -> None:
        refresh_force_values.append(force)

    service._ensure_list_sessions_refresh_task = ensure_list_sessions_refresh_task

    service._observe_run_event_for_snapshot_dirty(_run_event(RunEventType.RUN_STARTED))

    assert service._list_sessions_cache_dirty is True
    assert service._list_sessions_cache_version == 1
    assert refresh_force_values == [True]


def test_fast_rounds_snapshot_returns_cached_response_without_thread_hop() -> None:
    service = _snapshot_service()
    key = "session-1|8||0|0"
    service._rounds_snapshot_cache[key] = _RecoverySnapshotCacheEntry(
        snapshot={"items": ["cached"]},
        updated_monotonic=monotonic(),
    )

    snapshot = service.get_fast_cached_session_rounds_snapshot("session-1")

    assert snapshot is not None
    assert snapshot["items"] == ["cached"]
    assert snapshot["stale"] is False
    assert snapshot["snapshot_cache_hit"] is True


@pytest.mark.asyncio
async def test_rounds_snapshot_cache_fresh_hit_skips_projection_refresh() -> None:
    service = _snapshot_service()
    key = "session-1|8||0|0"
    service._rounds_snapshot_cache[key] = _RecoverySnapshotCacheEntry(
        snapshot={"items": ["cached"]},
        updated_monotonic=asyncio.get_running_loop().time(),
    )

    async def fail_build_rounds_snapshot_async(
        session_id: str,
        *,
        limit: int,
        cursor_run_id: str | None,
        timeline: bool,
        summary: bool,
    ) -> dict[str, object]:
        _ = (session_id, limit, cursor_run_id, timeline, summary)
        raise AssertionError("fresh cache hit should not rebuild rounds")

    service._build_rounds_snapshot_async = fail_build_rounds_snapshot_async

    snapshot = await service.get_cached_session_rounds_async("session-1")

    assert snapshot["items"] == ["cached"]
    assert snapshot["stale"] is False
    assert snapshot["snapshot_cache_hit"] is True


@pytest.mark.asyncio
async def test_rounds_snapshot_cache_stale_hit_returns_and_refreshes_once() -> None:
    service = _snapshot_service()
    key = "session-1|8||0|0"
    entry = _RecoverySnapshotCacheEntry(
        snapshot={"items": ["old"]},
        updated_monotonic=asyncio.get_running_loop().time() - 10.0,
    )
    service._rounds_snapshot_cache[key] = entry
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    refresh_count = 0

    async def build_rounds_snapshot_async(
        session_id: str,
        *,
        limit: int,
        cursor_run_id: str | None,
        timeline: bool,
        summary: bool,
    ) -> dict[str, object]:
        nonlocal refresh_count
        _ = (session_id, limit, cursor_run_id, timeline, summary)
        refresh_count += 1
        refresh_started.set()
        await release_refresh.wait()
        return {"items": ["new"]}

    service._build_rounds_snapshot_async = build_rounds_snapshot_async

    first = await service.get_cached_session_rounds_async("session-1")
    second = await service.get_cached_session_rounds_async("session-1")
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    release_refresh.set()
    await asyncio.gather(*service._rounds_refresh_tasks.values())

    assert first["items"] == ["old"]
    assert first["stale"] is True
    assert second["items"] == ["old"]
    assert refresh_count == 1
    assert service._rounds_snapshot_cache[key].snapshot["items"] == ["new"]


@pytest.mark.asyncio
async def test_rounds_snapshot_requires_fresh_read_returns_stale_then_refreshes() -> (
    None
):
    service = _snapshot_service()
    key = "session-1|8||0|0"
    entry = _RecoverySnapshotCacheEntry(
        snapshot={"items": ["old"]},
        updated_monotonic=asyncio.get_running_loop().time(),
    )
    entry.dirty = True
    entry.requires_fresh_read = True
    service._rounds_snapshot_cache[key] = entry
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    build_count = 0

    async def build_rounds_snapshot_async(
        session_id: str,
        *,
        limit: int,
        cursor_run_id: str | None,
        timeline: bool,
        summary: bool,
    ) -> dict[str, object]:
        nonlocal build_count
        _ = (session_id, limit, cursor_run_id, timeline, summary)
        build_count += 1
        refresh_started.set()
        await release_refresh.wait()
        return {"items": ["fresh"]}

    service._build_rounds_snapshot_async = build_rounds_snapshot_async

    fast_snapshot = service.get_fast_cached_session_rounds_snapshot("session-1")
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    snapshot = await service.get_cached_session_rounds_async("session-1")
    release_refresh.set()
    await asyncio.gather(*service._rounds_refresh_tasks.values())

    assert fast_snapshot is not None
    assert fast_snapshot["items"] == ["old"]
    assert fast_snapshot["stale"] is True
    assert snapshot["items"] == ["old"]
    assert snapshot["stale"] is True
    assert service._rounds_snapshot_cache[key].snapshot["items"] == ["fresh"]
    assert build_count == 1


@pytest.mark.asyncio
async def test_rounds_snapshot_cold_miss_builds_once_within_fast_budget() -> None:
    service = _snapshot_service()
    key = "session-1|8||0|0"
    build_count = 0

    async def build_rounds_snapshot_async(
        session_id: str,
        *,
        limit: int,
        cursor_run_id: str | None,
        timeline: bool,
        summary: bool,
    ) -> dict[str, object]:
        nonlocal build_count
        _ = (session_id, limit, cursor_run_id, timeline, summary)
        build_count += 1
        return {"items": ["new"]}

    service._build_rounds_snapshot_async = build_rounds_snapshot_async

    snapshot = await service.get_cached_session_rounds_async("session-1")

    assert snapshot["items"] == ["new"]
    assert snapshot["stale"] is False
    assert snapshot["snapshot_cache_hit"] is False
    assert build_count == 1
    assert service._rounds_snapshot_cache[key].snapshot["items"] == ["new"]


@pytest.mark.asyncio
async def test_recovery_snapshot_cold_miss_builds_once_within_fast_budget() -> None:
    service = _snapshot_service()
    build_count = 0

    async def get_recovery_snapshot_async(session_id: str) -> dict[str, object]:
        nonlocal build_count
        _ = session_id
        build_count += 1
        return {"active_run": {"run_id": "new-run"}}

    service.get_recovery_snapshot_async = get_recovery_snapshot_async

    snapshot = await service.get_cached_recovery_snapshot_async("session-1")

    assert snapshot["active_run"] == {"run_id": "new-run"}
    assert snapshot["stale"] is False
    assert snapshot["snapshot_cache_hit"] is False
    assert build_count == 1
    session_repo = cast(_SessionRepo, service._session_repo)
    assert session_repo.get_count == 1
    assert service._recovery_snapshot_cache["session-1"].snapshot["active_run"] == {
        "run_id": "new-run"
    }


@pytest.mark.asyncio
async def test_fast_recovery_snapshot_stale_dirty_hit_refreshes_once() -> None:
    service = _snapshot_service()
    entry = _RecoverySnapshotCacheEntry(
        snapshot={"active_run": None},
        updated_monotonic=asyncio.get_running_loop().time() - 10.0,
    )
    entry.dirty = True
    service._recovery_snapshot_cache["session-1"] = entry
    refresh_started = asyncio.Event()
    release_refresh = Event()
    refresh_count = 0

    def get_recovery_snapshot(session_id: str) -> dict[str, object]:
        nonlocal refresh_count
        _ = session_id
        refresh_count += 1
        refresh_started.set()
        release_refresh.wait(timeout=1)
        return {"active_run": {"run_id": "fresh-run"}}

    service.get_recovery_snapshot = get_recovery_snapshot

    first = service.get_fast_cached_recovery_snapshot("session-1")
    second = service.get_fast_cached_recovery_snapshot("session-1")
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    release_refresh.set()
    await asyncio.gather(*service._recovery_refresh_tasks.values())

    assert first is not None
    assert second is not None
    assert first["active_run"] is None
    assert first["stale"] is True
    assert second["active_run"] is None
    assert refresh_count == 1
    assert service._recovery_snapshot_cache["session-1"].snapshot["active_run"] == {
        "run_id": "fresh-run"
    }


@pytest.mark.asyncio
async def test_recovery_snapshot_requires_fresh_read_returns_stale_then_refreshes() -> (
    None
):
    service = _snapshot_service()
    entry = _RecoverySnapshotCacheEntry(
        snapshot={"active_run": None},
        updated_monotonic=asyncio.get_running_loop().time(),
    )
    entry.requires_fresh_read = True
    service._recovery_snapshot_cache["session-1"] = entry
    refresh_started = asyncio.Event()
    release_refresh = Event()
    refresh_count = 0

    def get_recovery_snapshot(session_id: str) -> dict[str, object]:
        nonlocal refresh_count
        _ = session_id
        refresh_count += 1
        refresh_started.set()
        release_refresh.wait(timeout=1)
        return {"active_run": {"run_id": "fresh-run"}}

    service.get_recovery_snapshot = get_recovery_snapshot

    fast_snapshot = service.get_fast_cached_recovery_snapshot("session-1")
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    snapshot = await service.get_cached_recovery_snapshot_async("session-1")
    release_refresh.set()
    await asyncio.gather(*service._recovery_refresh_tasks.values())

    assert fast_snapshot is not None
    assert fast_snapshot["active_run"] is None
    assert fast_snapshot["stale"] is True
    assert snapshot["active_run"] is None
    assert snapshot["stale"] is True
    assert service._recovery_snapshot_cache["session-1"].snapshot["active_run"] == {
        "run_id": "fresh-run"
    }
    assert refresh_count == 1


@pytest.mark.asyncio
async def test_agents_snapshot_cache_fresh_hit_skips_repository_scan() -> None:
    service = _snapshot_service()
    key = "agents|session-1"
    service._agents_snapshot_cache[key] = _RecoverySnapshotCacheEntry(
        snapshot={"items": [{"role_id": "cached"}]},
        updated_monotonic=asyncio.get_running_loop().time(),
    )

    async def fail_build_agents_snapshot_async(session_id: str) -> dict[str, object]:
        _ = session_id
        raise AssertionError("fresh agents cache hit should not rebuild")

    service._build_agents_snapshot_async = fail_build_agents_snapshot_async

    agents = await service.list_cached_agents_in_session_async("session-1")

    assert agents == ({"role_id": "cached"},)


@pytest.mark.asyncio
async def test_agents_snapshot_cache_cold_miss_returns_built_snapshot() -> None:
    service = _snapshot_service()
    build_count = 0

    async def build_agents_snapshot_async(session_id: str) -> dict[str, object]:
        nonlocal build_count
        assert session_id == "session-1"
        build_count += 1
        return {
            "items": [{"role_id": "coordinator", "instance_id": "agent-1"}],
            "snapshot_refresh_ms": 1,
            "snapshot_cache_hit": False,
        }

    service._build_agents_snapshot_async = build_agents_snapshot_async

    agents = await service.list_cached_agents_in_session_async("session-1")

    assert agents == ({"role_id": "coordinator", "instance_id": "agent-1"},)
    assert build_count == 1
    cached = service._agents_snapshot_cache["agents|session-1"]
    assert cached.snapshot["items"] == [
        {"role_id": "coordinator", "instance_id": "agent-1"}
    ]
    assert cached.dirty is False


@pytest.mark.asyncio
async def test_tasks_snapshot_cache_cold_miss_returns_built_snapshot() -> None:
    service = _snapshot_service()
    build_count = 0

    async def build_tasks_snapshot_async(session_id: str) -> dict[str, object]:
        nonlocal build_count
        assert session_id == "session-1"
        build_count += 1
        return {
            "items": [{"task_id": "task-1", "status": "running"}],
            "snapshot_refresh_ms": 1,
            "snapshot_cache_hit": False,
        }

    service._build_tasks_snapshot_async = build_tasks_snapshot_async

    tasks = await service.list_cached_session_tasks_async("session-1")

    assert tasks == ({"task_id": "task-1", "status": "running"},)
    assert build_count == 1
    cached = service._tasks_snapshot_cache["tasks|session-1"]
    assert cached.snapshot["items"] == [{"task_id": "task-1", "status": "running"}]
    assert cached.dirty is False


@pytest.mark.asyncio
async def test_tasks_snapshot_stale_hit_refreshes_once() -> None:
    service = _snapshot_service()
    key = "tasks|session-1"
    service._tasks_snapshot_cache[key] = _RecoverySnapshotCacheEntry(
        snapshot={"items": [{"task_id": "old"}]},
        updated_monotonic=asyncio.get_running_loop().time() - 10.0,
    )
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    refresh_count = 0

    async def build_tasks_snapshot_async(session_id: str) -> dict[str, object]:
        nonlocal refresh_count
        _ = session_id
        refresh_count += 1
        refresh_started.set()
        await release_refresh.wait()
        return {"items": [{"task_id": "new"}]}

    service._build_tasks_snapshot_async = build_tasks_snapshot_async

    first = await service.list_cached_session_tasks_async("session-1")
    second = await service.list_cached_session_tasks_async("session-1")
    tasks = tuple(service._tasks_refresh_tasks.values())
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    release_refresh.set()
    await asyncio.gather(*tasks)

    assert first == ({"task_id": "old"},)
    assert second == ({"task_id": "old"},)
    assert refresh_count == 1
    assert service._tasks_snapshot_cache[key].snapshot["items"] == [{"task_id": "new"}]


@pytest.mark.asyncio
async def test_token_usage_snapshot_cold_miss_returns_built_snapshot() -> None:
    service = _snapshot_service()
    key = "token_usage|session-1"

    async def build_token_usage_snapshot_async(session_id: str) -> dict[str, object]:
        _ = session_id
        return {"session_id": "session-1", "total_tokens": 42, "by_role": {}}

    service._build_token_usage_snapshot_async = build_token_usage_snapshot_async

    snapshot = await service.get_cached_token_usage_by_session_snapshot_async(
        "session-1"
    )

    assert snapshot["session_id"] == "session-1"
    assert snapshot["stale"] is False
    assert snapshot["total_tokens"] == 42
    assert service._token_usage_snapshot_cache[key].snapshot["total_tokens"] == 42


@pytest.mark.asyncio
async def test_token_usage_snapshot_cold_miss_timeout_returns_stale_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _snapshot_service()
    key = "token_usage|session-1"
    refresh_started = asyncio.Event()

    async def build_token_usage_snapshot_async(session_id: str) -> dict[str, object]:
        _ = session_id
        refresh_started.set()
        await asyncio.sleep(0.1)
        return {"session_id": "session-1", "total_tokens": 42, "by_role": {}}

    service._build_token_usage_snapshot_async = build_token_usage_snapshot_async
    monkeypatch.setattr(
        session_read_models_module,
        "LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS",
        0.01,
    )

    snapshot = await service.get_cached_token_usage_by_session_snapshot_async(
        "session-1"
    )

    assert snapshot["session_id"] == "session-1"
    assert snapshot["stale"] is True
    assert snapshot["total_tokens"] == 0
    assert service._token_usage_snapshot_cache[key].snapshot["total_tokens"] == 0


def test_token_usage_snapshot_projects_session_summary() -> None:
    snapshot = SessionService._token_usage_snapshot(
        SessionTokenUsage(
            session_id="session-1",
            total_input_tokens=10,
            total_cached_input_tokens=2,
            total_output_tokens=3,
            total_reasoning_output_tokens=1,
            total_tokens=13,
            total_requests=4,
            total_tool_calls=5,
            by_role={},
        )
    )

    assert snapshot["session_id"] == "session-1"
    assert snapshot["total_tokens"] == 13
    assert snapshot["snapshot_cache_hit"] is False
