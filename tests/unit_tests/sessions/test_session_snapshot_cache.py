from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from relay_teams.agent_runtimes.instances.enums import InstanceStatus
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_list_cache import SessionListCache
from relay_teams.sessions.session_models import SessionMetadataPatch
from relay_teams.sessions.session_models import SessionRecord
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_read_models import SessionSnapshotSection
from relay_teams.sessions.session_service import SessionService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.session_snapshot_cache import SessionSnapshotCache
from relay_teams.sessions.session_snapshot_cache import resolve_positive_int_env


class _CountingRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.block_next = False
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.fail_next = False

    async def __call__(
        self,
        operation: str,
        refresh: Callable[[], object],
    ) -> object:
        _ = operation
        self.calls += 1
        if self.block_next:
            self.started.set()
            await self.release.wait()
            self.block_next = False
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("refresh failed")
        return refresh()


class _SelectiveBlockingRunner:
    def __init__(self, block_calls: set[int]) -> None:
        self.block_calls = block_calls
        self.calls = 0
        self.started_blocking_call = asyncio.Event()
        self.release_blocked_call = asyncio.Event()

    async def __call__(
        self,
        operation: str,
        refresh: Callable[[], object],
    ) -> object:
        _ = operation
        self.calls += 1
        call_number = self.calls
        if call_number in self.block_calls:
            self.started_blocking_call.set()
            await self.release_blocked_call.wait()
        return refresh()


def test_session_snapshot_cache_env_resolver_ignores_invalid_overrides() -> None:
    with patch.dict("os.environ", {"RELAY_TEAMS_TEST_CACHE_MS": "not-an-int"}):
        assert resolve_positive_int_env("RELAY_TEAMS_TEST_CACHE_MS", 123) == 123

    with patch.dict("os.environ", {"RELAY_TEAMS_TEST_CACHE_MS": "0"}):
        assert resolve_positive_int_env("RELAY_TEAMS_TEST_CACHE_MS", 123) == 123

    with patch.dict("os.environ", {"RELAY_TEAMS_TEST_CACHE_MS": "250"}):
        assert resolve_positive_int_env("RELAY_TEAMS_TEST_CACHE_MS", 123) == 250


@pytest.mark.asyncio
async def test_session_list_cache_rejects_invalid_runner_result() -> None:
    async def invalid_runner(
        operation: str,
        refresh: Callable[[], object],
    ) -> object:
        _ = (operation, refresh)
        return ["not", "a", "tuple"]

    cache = SessionListCache(refresh_runner=invalid_runner)

    with pytest.raises(TypeError, match="Session list refresh returned"):
        await cache.read(lambda: ())


@pytest.mark.asyncio
async def test_session_snapshot_cache_refreshes_empty_cache() -> None:
    runner = _CountingRunner()
    values = ["fresh"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )

    result = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.SUBAGENTS,
        refresh=lambda: tuple(values),
    )

    assert result.value == ("fresh",)
    assert result.diagnostics.cache_hit is False
    assert result.diagnostics.stale is False
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_session_snapshot_cache_cold_miss_timeout_returns_fallback() -> None:
    runner = _CountingRunner()
    runner.block_next = True
    values = ["fresh"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        cold_miss_timeout_seconds=0.01,
    )

    result = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.SUBAGENTS,
        refresh=lambda: tuple(values),
        fallback=tuple,
    )

    assert result.value == ()
    assert result.diagnostics.cache_hit is False
    assert result.diagnostics.stale is True
    assert result.diagnostics.refresh_in_progress is True
    assert await _wait_for_event(runner.started)
    runner.release.set()
    await asyncio.sleep(0)

    refreshed = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.SUBAGENTS,
        refresh=lambda: tuple(values),
    )
    assert refreshed.value == ("fresh",)
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_session_list_cache_cold_miss_waits_for_projection() -> None:
    runner = _CountingRunner()
    runner.block_next = True
    cache = SessionListCache(
        refresh_runner=runner,
        cold_miss_timeout_seconds=0.01,
    )
    expected = (SessionRecord(session_id="session-1", workspace_id="default"),)
    read_task = asyncio.create_task(cache.read(lambda: expected))

    assert await _wait_for_event(runner.started)
    await asyncio.sleep(0.03)
    assert read_task.done() is False
    runner.release.set()
    result = await read_task

    assert result.value == expected
    assert result.diagnostics.cache_hit is False
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_session_list_cache_clear_blocks_in_flight_repopulation() -> None:
    runner = _CountingRunner()
    cache = SessionListCache(refresh_runner=runner)
    first_record = SessionRecord(session_id="session-1", workspace_id="default")
    second_record = SessionRecord(session_id="session-2", workspace_id="default")
    runner.block_next = True
    read_task = asyncio.create_task(cache.read(lambda: (first_record,)))

    assert await _wait_for_event(runner.started)
    cache.clear()
    runner.release.set()
    with pytest.raises(RuntimeError):
        await read_task

    result = await cache.read(lambda: (second_record,))

    assert result.value == (second_record,)
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_session_list_cache_merge_preserves_existing_order() -> None:
    runner = _CountingRunner()
    cache = SessionListCache(refresh_runner=runner)
    newest = SessionRecord(session_id="newest", workspace_id="default")
    older = SessionRecord(session_id="older", workspace_id="default")
    _ = await cache.read(lambda: (newest, older))

    renamed_older = older.model_copy(
        update={"metadata": {"title": "Renamed older session"}},
    )
    cache.merge_record(renamed_older)
    result = await cache.read(lambda: ())

    assert [record.session_id for record in result.value] == ["newest", "older"]
    assert result.value[1].metadata == {"title": "Renamed older session"}


@pytest.mark.asyncio
async def test_session_list_cache_remove_blocks_stale_background_refresh() -> None:
    runner = _CountingRunner()
    with patch.dict(
        "os.environ",
        {"RELAY_TEAMS_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS": "50"},
    ):
        cache = SessionListCache(refresh_runner=runner)
    first_record = SessionRecord(session_id="session-1", workspace_id="default")
    second_record = SessionRecord(session_id="session-2", workspace_id="default")
    _ = await cache.read(lambda: (first_record, second_record))

    await asyncio.sleep(0.06)
    runner.block_next = True
    cache.mark_dirty()
    stale = await cache.read(lambda: (first_record, second_record))

    assert stale.value == (first_record, second_record)
    assert await _wait_for_event(runner.started)

    cache.remove_record("session-1")
    runner.release.set()
    for _ in range(20):
        if not runner.block_next:
            break
        await asyncio.sleep(0.01)

    result = await cache.read(lambda: (first_record, second_record))

    assert runner.block_next is False
    assert result.value == (second_record,)


@pytest.mark.asyncio
async def test_session_snapshot_cache_returns_stale_and_refreshes_background() -> None:
    runner = _CountingRunner()
    values = ["v1"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )
    first = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )
    assert first.value == {"value": "v1"}

    values.append("v2")
    runner.block_next = True
    cache.mark_session_dirty("session-1")
    stale = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    assert stale.value == {"value": "v1"}
    assert stale.diagnostics.cache_hit is True
    assert stale.diagnostics.stale is True
    assert stale.diagnostics.refresh_in_progress is True
    assert await _wait_for_event(runner.started)
    runner.release.set()
    await asyncio.sleep(0)

    refreshed = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )
    assert refreshed.value == {"value": "v2"}


@pytest.mark.asyncio
async def test_session_snapshot_cache_requires_fresh_read_waits_for_refresh() -> None:
    runner = _CountingRunner()
    values = ["v1"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=60,
    )
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    values.append("v2")
    runner.block_next = True
    cache.mark_session_dirty("session-1", requires_fresh_read=True)
    read_task = asyncio.create_task(
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.RECOVERY,
            refresh=lambda: {"value": values[-1]},
        )
    )

    assert await _wait_for_event(runner.started)
    assert read_task.done() is False
    runner.release.set()
    result = await read_task

    assert result.value == {"value": "v2"}
    assert result.diagnostics.cache_hit is False
    assert result.diagnostics.stale is False


@pytest.mark.asyncio
async def test_session_snapshot_cache_fresh_timeout_keeps_stale_value() -> None:
    runner = _CountingRunner()
    values = ["v1"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        cold_miss_timeout_seconds=0.01,
        refresh_min_interval_seconds=0,
    )
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    values.append("v2")
    runner.block_next = True
    cache.mark_session_dirty("session-1", requires_fresh_read=True)
    stale = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
        fallback=lambda: {"value": "empty"},
    )

    assert stale.value == {"value": "v1"}
    assert stale.diagnostics.cache_hit is True
    assert stale.diagnostics.stale is True
    assert stale.diagnostics.refresh_in_progress is True
    assert await _wait_for_event(runner.started)
    runner.release.set()


@pytest.mark.asyncio
async def test_session_snapshot_cache_force_refresh_skips_stale_in_flight_refresh() -> (
    None
):
    runner = _SelectiveBlockingRunner(block_calls={2})
    values = ["v1"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    values.append("v2")
    cache.mark_session_dirty("session-1")
    stale = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )
    assert stale.value == {"value": "v1"}
    assert await _wait_for_event(runner.started_blocking_call)

    values.append("v3")
    cache.mark_session_dirty("session-1")
    fresh = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
        force_refresh=True,
    )

    assert fresh.value == {"value": "v3"}
    assert runner.calls == 3
    runner.release_blocked_call.set()
    await asyncio.sleep(0)
    after_old_refresh = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
        force_refresh=True,
    )
    assert after_old_refresh.value == {"value": "v3"}


@pytest.mark.asyncio
async def test_session_snapshot_cache_coalesces_background_refresh() -> None:
    runner = _CountingRunner()
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )
    values = ["v1"]
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.TASKS,
        refresh=lambda: tuple(values),
    )

    values.append("v2")
    runner.block_next = True
    cache.mark_session_dirty("session-1")
    results = await asyncio.gather(
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.TASKS,
            refresh=lambda: tuple(values),
        ),
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.TASKS,
            refresh=lambda: tuple(values),
        ),
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.TASKS,
            refresh=lambda: tuple(values),
        ),
    )

    assert [result.value for result in results] == [("v1",), ("v1",), ("v1",)]
    assert runner.calls == 2
    assert await _wait_for_event(runner.started)
    runner.release.set()


@pytest.mark.asyncio
async def test_session_snapshot_cache_throttles_dirty_refreshes() -> None:
    runner = _CountingRunner()
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=60,
    )
    values = ["v1"]
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.ROUNDS,
        refresh=lambda: tuple(values),
    )

    values.append("v2")
    cache.mark_session_dirty("session-1")
    results = await asyncio.gather(
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.ROUNDS,
            refresh=lambda: tuple(values),
        ),
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.ROUNDS,
            refresh=lambda: tuple(values),
        ),
    )

    assert [result.value for result in results] == [("v1",), ("v1",)]
    assert runner.calls == 1
    assert all(result.diagnostics.stale for result in results)


@pytest.mark.asyncio
async def test_session_snapshot_cache_force_refresh_waits_for_latest() -> None:
    runner = _CountingRunner()
    cache = SessionSnapshotCache(refresh_runner=runner)
    values = ["v1"]
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.AGENTS,
        refresh=lambda: (values[-1],),
    )

    values.append("v2")
    result = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.AGENTS,
        refresh=lambda: (values[-1],),
        force_refresh=True,
    )

    assert result.value == ("v2",)
    assert result.diagnostics.cache_hit is False
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_session_snapshot_cache_force_refresh_returns_result_if_dirty_races() -> (
    None
):
    runner = _CountingRunner()
    values = ["v1"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.TOKEN_USAGE,
        refresh=lambda: {"value": values[-1]},
    )

    values.append("v2")
    runner.block_next = True
    read_task = asyncio.create_task(
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.TOKEN_USAGE,
            refresh=lambda: {"value": values[-1]},
            force_refresh=True,
        )
    )

    assert await _wait_for_event(runner.started)
    cache.mark_session_dirty("session-1")
    runner.release.set()
    result = await read_task

    assert result.value == {"value": "v2"}
    assert result.diagnostics.cache_hit is False
    assert result.diagnostics.dirty is True


@pytest.mark.asyncio
async def test_session_snapshot_cache_cold_miss_failure_returns_fallback() -> None:
    runner = _CountingRunner()
    runner.fail_next = True
    cache = SessionSnapshotCache(refresh_runner=runner)

    result = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.ROUNDS,
        refresh=lambda: {"items": [{"run_id": "run-1"}]},
        fallback=lambda: {"items": [], "next_cursor": None},
    )

    assert result.value == {"items": [], "next_cursor": None}
    assert result.diagnostics.cache_hit is False
    assert result.diagnostics.refresh_error == "RuntimeError: refresh failed"


@pytest.mark.asyncio
async def test_session_snapshot_cache_dirty_during_cold_miss_keeps_value() -> None:
    runner = _CountingRunner()
    runner.block_next = True
    cache = SessionSnapshotCache(refresh_runner=runner)
    read_task = asyncio.create_task(
        cache.read(
            session_id="session-1",
            section=SessionSnapshotSection.RECOVERY,
            refresh=lambda: {"value": "fresh"},
        )
    )

    assert await _wait_for_event(runner.started)
    cache.mark_session_dirty("session-1")
    runner.release.set()
    result = await read_task

    assert result.value == {"value": "fresh"}


@pytest.mark.asyncio
async def test_session_snapshot_cache_preserves_dirty_when_refresh_races_event() -> (
    None
):
    runner = _CountingRunner()
    values = ["v1"]
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    values.append("v2")
    runner.block_next = True
    cache.mark_session_dirty("session-1")
    stale = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    assert stale.value == {"value": "v1"}
    assert await _wait_for_event(runner.started)
    cache.mark_session_dirty("session-1", requires_fresh_read=True)
    runner.release.set()
    await asyncio.sleep(0)

    values.append("v3")
    fresh = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"value": values[-1]},
    )

    assert fresh.value == {"value": "v3"}
    assert runner.calls == 3


@pytest.mark.asyncio
async def test_session_snapshot_cache_bounds_distinct_keys() -> None:
    runner = _CountingRunner()
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        max_entries=2,
    )

    for session_id in ("session-1", "session-2", "session-3"):
        result = await cache.read(
            session_id=session_id,
            section=SessionSnapshotSection.RECOVERY,
            refresh=lambda current=session_id: {"session_id": current},
        )
        assert result.value == {"session_id": session_id}

    reread = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.RECOVERY,
        refresh=lambda: {"session_id": "session-1"},
    )

    assert reread.value == {"session_id": "session-1"}
    assert reread.diagnostics.cache_hit is False
    assert runner.calls == 4


@pytest.mark.asyncio
async def test_session_snapshot_cache_preserves_stale_after_refresh_failure() -> None:
    runner = _CountingRunner()
    cache = SessionSnapshotCache(
        refresh_runner=runner,
        refresh_min_interval_seconds=0,
    )
    values = ["v1"]
    _ = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.TOKEN_USAGE,
        refresh=lambda: tuple(values),
    )

    values.append("v2")
    runner.fail_next = True
    cache.mark_session_dirty("session-1")
    stale = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.TOKEN_USAGE,
        refresh=lambda: tuple(values),
    )
    await asyncio.sleep(0)

    assert stale.value == ("v1",)
    assert stale.diagnostics.cache_hit is True
    after_failure = await cache.read(
        session_id="session-1",
        section=SessionSnapshotSection.TOKEN_USAGE,
        refresh=lambda: tuple(values),
    )
    assert after_failure.value == ("v1",)
    assert after_failure.diagnostics.refresh_error is not None

    with pytest.raises(RuntimeError, match="force failure"):
        await cache.read(
            session_id="session-2",
            section=SessionSnapshotSection.TOKEN_USAGE,
            refresh=_raise_force_failure,
            force_refresh=True,
        )


def _raise_force_failure() -> tuple[str, ...]:
    raise RuntimeError("force failure")


async def _wait_for_event(event: asyncio.Event) -> bool:
    try:
        await asyncio.wait_for(event.wait(), timeout=1.0)
    except TimeoutError:
        return False
    return True


@pytest.mark.asyncio
async def test_session_service_list_cache_dirty_after_session_create(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-list-cache.db", runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    first = await service.list_sessions_async()
    assert [record.session_id for record in first] == ["session-1"]

    _ = service.create_session(session_id="session-2", workspace_id="default")
    stale = await service.list_sessions_async()

    assert [record.session_id for record in stale] == ["session-2", "session-1"]
    fresh = await service.list_sessions_async(force_refresh=True)
    assert sorted(record.session_id for record in fresh) == ["session-1", "session-2"]


@pytest.mark.asyncio
async def test_session_service_delete_clears_session_list_cache(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-list-delete-cache.db", runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = service.create_session(session_id="session-2", workspace_id="default")
    _ = await service.list_sessions_async()

    service.delete_session("session-1", cascade=True)
    result = await service.list_sessions_async()

    assert [record.session_id for record in result] == ["session-2"]


@pytest.mark.asyncio
async def test_session_service_terminal_event_requires_fresh_session_list(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-list-terminal-cache.db", runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = await service.list_sessions_async()

    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.RUN_COMPLETED,
        )
    )
    read_task = asyncio.create_task(service.list_sessions_async())

    assert await _wait_for_event(runner.started)
    assert read_task.done() is False
    runner.release.set()
    sessions = await read_task
    assert [session.session_id for session in sessions] == ["session-1"]


@pytest.mark.asyncio
async def test_session_service_background_task_event_marks_session_list_dirty(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(
        tmp_path / "session-list-background-task-cache.db", runner=runner
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = await service.list_sessions_async()

    await asyncio.sleep(0.55)
    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.BACKGROUND_TASK_STARTED,
        )
    )
    stale = await service.list_sessions_async()

    assert [session.session_id for session in stale] == ["session-1"]
    assert await _wait_for_event(runner.started)
    runner.release.set()


@pytest.mark.asyncio
async def test_session_service_todo_event_requires_fresh_snapshot_read(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-todo-fresh-cache.db", runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = await service.get_recovery_snapshot_async("session-1")

    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.TODO_UPDATED,
        )
    )
    read_task = asyncio.create_task(service.get_recovery_snapshot_async("session-1"))

    assert await _wait_for_event(runner.started)
    assert read_task.done() is False
    runner.release.set()
    snapshot = await read_task
    assert snapshot["active_run"] is None


@pytest.mark.parametrize(
    "event_type",
    [RunEventType.TOKEN_USAGE, RunEventType.USER_QUESTION_ANSWERED],
)
@pytest.mark.asyncio
async def test_session_service_event_requires_fresh_snapshot_read(
    tmp_path: Path,
    event_type: RunEventType,
) -> None:
    runner = _CountingRunner()
    service = _build_service(
        tmp_path / f"session-{event_type.value}-fresh-cache.db", runner=runner
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = await service.get_recovery_snapshot_async("session-1")

    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=event_type,
        )
    )
    read_task = asyncio.create_task(service.get_recovery_snapshot_async("session-1"))

    assert await _wait_for_event(runner.started)
    assert read_task.done() is False
    runner.release.set()
    snapshot = await read_task
    assert snapshot["active_run"] is None


@pytest.mark.asyncio
async def test_session_service_run_event_marks_session_snapshot_dirty(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    db_path = tmp_path / "session-run-event-cache.db"
    service = _build_service(db_path, runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    first = await service.get_recovery_snapshot_async("session-1")
    assert first["active_run"] is None

    _seed_root_task(db_path, run_id="run-1", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    await asyncio.sleep(0.55)
    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.RUN_STARTED,
        )
    )

    stale = await service.get_recovery_snapshot_async("session-1")

    assert stale["active_run"] is None
    assert await _wait_for_event(runner.started)
    runner.release.set()
    fresh = await service.get_recovery_snapshot_async(
        "session-1",
        force_refresh=True,
    )
    active_run = fresh["active_run"]
    assert isinstance(active_run, dict)
    assert active_run["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_session_service_terminal_event_skips_sync_merge_in_async_listener(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(
        tmp_path / "session-async-terminal-event.db", runner=runner
    )

    with patch.object(
        service,
        "_merge_terminal_session_projection_into_list_cache",
    ) as merge_terminal:
        service.mark_run_event_dirty(
            RunEvent(
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
                instance_id="inst-1",
                event_type=RunEventType.RUN_COMPLETED,
            )
        )

    merge_terminal.assert_not_called()


@pytest.mark.asyncio
async def test_session_service_tool_call_event_marks_session_snapshot_dirty(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    db_path = tmp_path / "session-tool-call-cache.db"
    service = _build_service(db_path, runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    first = await service.list_agents_in_session_async("session-1")
    assert first == ()

    AgentInstanceRepository(db_path).upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-writer",
        role_id="writer",
        workspace_id="default",
        conversation_id="conversation-writer",
        status=InstanceStatus.IDLE,
    )
    await asyncio.sleep(0.55)
    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.TOOL_CALL,
        )
    )

    stale = await service.list_agents_in_session_async("session-1")

    assert stale == ()
    assert await _wait_for_event(runner.started)
    runner.release.set()
    fresh = await service.list_agents_in_session_async(
        "session-1",
        force_refresh=True,
    )
    assert [agent["instance_id"] for agent in fresh] == ["inst-writer"]


@pytest.mark.asyncio
async def test_session_service_subagent_status_event_marks_session_snapshot_dirty(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    db_path = tmp_path / "session-subagent-status-cache.db"
    service = _build_service(db_path, runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    first = await service.list_agents_in_session_async("session-1")
    assert first == ()

    AgentInstanceRepository(db_path).upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-writer",
        role_id="writer",
        workspace_id="default",
        conversation_id="conversation-writer",
        status=InstanceStatus.IDLE,
    )
    await asyncio.sleep(0.55)
    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-writer",
            event_type=RunEventType.SUBAGENT_SESSION_STATUS_CHANGED,
        )
    )

    stale = await service.list_agents_in_session_async("session-1")

    assert stale == ()
    assert await _wait_for_event(runner.started)
    runner.release.set()
    fresh = await service.list_agents_in_session_async(
        "session-1",
        force_refresh=True,
    )
    assert [agent["instance_id"] for agent in fresh] == ["inst-writer"]


@pytest.mark.asyncio
async def test_session_service_pending_action_event_requires_fresh_session_list(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    db_path = tmp_path / "session-list-pending-action-cache.db"
    service = _build_service(db_path, runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-1", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    first = await service.list_sessions_async(force_refresh=True)
    assert first[0].pending_tool_approval_count == 0

    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="tool-call-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="test_tool",
        args_preview="{}",
    )
    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.TOOL_APPROVAL_REQUESTED,
        )
    )
    read_task = asyncio.create_task(service.list_sessions_async())

    assert await _wait_for_event(runner.started)
    assert read_task.done() is False
    runner.release.set()
    sessions = await read_task
    assert sessions[0].pending_tool_approval_count == 1


@pytest.mark.asyncio
async def test_session_service_round_projection_event_marks_snapshot_dirty(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-round-event-cache.db", runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    first = await service.get_session_rounds_async("session-1")
    assert first["items"] == []

    await asyncio.sleep(0.55)
    runner.block_next = True
    service.mark_run_event_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            instance_id="inst-1",
            event_type=RunEventType.MODEL_STEP_STARTED,
        )
    )

    stale = await service.get_session_rounds_async("session-1")

    assert stale["items"] == []
    assert await _wait_for_event(runner.started)
    runner.release.set()


@pytest.mark.asyncio
async def test_session_service_rounds_cold_miss_waits_for_projection(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-round-cold-cache.db", runner=runner)
    service._session_snapshot_cache = SessionSnapshotCache(
        refresh_runner=runner,
        cold_miss_timeout_seconds=0.01,
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")
    runner.block_next = True
    read_task = asyncio.create_task(service.get_session_rounds_async("session-1"))

    assert await _wait_for_event(runner.started)
    await asyncio.sleep(0.03)
    assert read_task.done() is False
    runner.release.set()
    snapshot = await read_task

    assert snapshot == {"items": [], "next_cursor": None, "has_more": False}
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_session_service_clear_messages_requires_fresh_snapshot_read(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    service = _build_service(tmp_path / "session-clear-cache.db", runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = await service.get_recovery_snapshot_async("session-1")

    runner.block_next = True
    assert service.clear_session_messages("session-1") == 0
    read_task = asyncio.create_task(service.get_recovery_snapshot_async("session-1"))

    assert await _wait_for_event(runner.started)
    assert read_task.done() is False
    runner.release.set()
    snapshot = await read_task
    assert snapshot["active_run"] is None


@pytest.mark.asyncio
async def test_session_service_update_merges_enriched_session_list_row(
    tmp_path: Path,
) -> None:
    runner = _CountingRunner()
    db_path = tmp_path / "session-list-update-enriched.db"
    service = _build_service(db_path, runner=runner)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-1", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    first = await service.list_sessions_async(force_refresh=True)
    assert first[0].has_active_run is True
    assert first[0].active_run_id == "run-1"

    service.update_session("session-1", SessionMetadataPatch(title="Renamed"))
    stale = await service.list_sessions_async()

    assert stale[0].metadata["title"] == "Renamed"
    assert stale[0].has_active_run is True
    assert stale[0].active_run_id == "run-1"


def _build_service(db_path: Path, *, runner: _CountingRunner) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=None,
        event_log=EventLog(db_path),
        projection_refresh_runner=runner,
    )


def _seed_root_task(db_path: Path, *, run_id: str, session_id: str) -> None:
    _ = TaskRepository(db_path).create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="do work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
