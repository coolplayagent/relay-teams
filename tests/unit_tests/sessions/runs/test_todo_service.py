# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.runs.todo_models import TodoItem, TodoStatus
from relay_teams.sessions.runs.todo_repository import TodoRepository
from relay_teams.sessions.runs.todo_service import MAX_TODO_TOTAL_CHARS, TodoService


def _build_service(db_path: Path) -> tuple[TodoService, EventLog]:
    event_log = EventLog(db_path)
    service = TodoService(
        repository=TodoRepository(db_path),
        run_event_hub=RunEventHub(
            event_log=event_log,
            run_state_repo=RunStateRepository(db_path),
        ),
    )
    return service, event_log


def test_replace_for_run_persists_snapshot_and_publishes_event(tmp_path: Path) -> None:
    service, event_log = _build_service(tmp_path / "todo.db")

    snapshot = service.replace_for_run(
        run_id="run-1",
        session_id="session-1",
        items=(
            TodoItem(content="Inspect repo", status=TodoStatus.COMPLETED),
            TodoItem(content="Implement feature", status=TodoStatus.IN_PROGRESS),
        ),
        updated_by_role_id="MainAgent",
        updated_by_instance_id="inst-1",
    )

    assert snapshot.version == 1
    assert snapshot.updated_by_role_id == "MainAgent"
    persisted = service.get_for_run(run_id="run-1", session_id="session-1")
    assert persisted.items == snapshot.items

    events = event_log.list_by_trace("run-1")
    assert len(events) == 1
    assert events[0]["event_type"] == RunEventType.TODO_UPDATED.value
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload["items"][1]["status"] == "in_progress"


def test_replace_for_run_overwrites_existing_snapshot_and_increments_version(
    tmp_path: Path,
) -> None:
    service, _event_log = _build_service(tmp_path / "todo-overwrite.db")

    first = service.replace_for_run(
        run_id="run-1",
        session_id="session-1",
        items=(TodoItem(content="First step", status=TodoStatus.PENDING),),
    )
    second = service.replace_for_run(
        run_id="run-1",
        session_id="session-1",
        items=(TodoItem(content="Second step", status=TodoStatus.COMPLETED),),
    )

    assert first.version == 1
    assert second.version == 2
    assert [item.content for item in second.items] == ["Second step"]


def test_get_for_run_returns_empty_snapshot_when_missing(tmp_path: Path) -> None:
    service, _event_log = _build_service(tmp_path / "todo-empty.db")

    snapshot = service.get_for_run(run_id="run-404", session_id="session-1")

    assert snapshot.run_id == "run-404"
    assert snapshot.session_id == "session-1"
    assert snapshot.items == ()
    assert snapshot.version == 0


def test_replace_for_run_rejects_multiple_in_progress_items(tmp_path: Path) -> None:
    service, _event_log = _build_service(tmp_path / "todo-invalid.db")

    with pytest.raises(ValueError, match="at most one in_progress"):
        service.replace_for_run(
            run_id="run-1",
            session_id="session-1",
            items=(
                TodoItem(content="One", status=TodoStatus.IN_PROGRESS),
                TodoItem(content="Two", status=TodoStatus.IN_PROGRESS),
            ),
        )


def test_replace_for_run_rejects_oversized_payload(tmp_path: Path) -> None:
    service, _event_log = _build_service(tmp_path / "todo-oversized.db")
    items = tuple(
        TodoItem(content=f"item-{index}-" + ("x" * 313), status=TodoStatus.PENDING)
        for index in range(50)
    )
    assert sum(len(item.content) for item in items) > MAX_TODO_TOTAL_CHARS

    with pytest.raises(ValueError, match="maximum content size"):
        service.replace_for_run(
            run_id="run-1",
            session_id="session-1",
            items=items,
        )


def test_clear_for_run_persists_empty_list(tmp_path: Path) -> None:
    service, _event_log = _build_service(tmp_path / "todo-clear.db")
    _ = service.replace_for_run(
        run_id="run-1",
        session_id="session-1",
        items=(TodoItem(content="Something", status=TodoStatus.PENDING),),
    )

    cleared = service.clear_for_run(run_id="run-1", session_id="session-1")

    assert cleared.version == 2
    assert cleared.items == ()


@pytest.mark.asyncio
async def test_async_replace_for_run_persists_snapshot_and_publishes_event(
    tmp_path: Path,
) -> None:
    service, event_log = _build_service(tmp_path / "todo-async.db")

    try:
        snapshot = await service.replace_for_run_async(
            run_id="run-1",
            session_id="session-1",
            items=(TodoItem(content="Inspect repo", status=TodoStatus.COMPLETED),),
            updated_by_role_id="MainAgent",
            updated_by_instance_id="inst-1",
        )
        persisted = await service.get_for_run_async(
            run_id="run-1",
            session_id="session-1",
        )
        events = await event_log.list_by_trace_async("run-1")
    finally:
        await event_log.close_async()

    assert snapshot.version == 1
    assert persisted.items == snapshot.items
    assert len(events) == 1
    assert events[0]["event_type"] == RunEventType.TODO_UPDATED.value


@pytest.mark.asyncio
async def test_async_todo_service_lists_clears_and_deletes(tmp_path: Path) -> None:
    service, event_log = _build_service(tmp_path / "todo-async-lifecycle.db")

    try:
        missing = await service.get_for_run_async(
            run_id="run-missing",
            session_id="session-1",
        )
        first = await service.replace_for_run_async(
            run_id="run-1",
            session_id="session-1",
            items=(TodoItem(content="Inspect repo", status=TodoStatus.PENDING),),
        )
        second = await service.replace_for_run_async(
            run_id="run-2",
            session_id="session-1",
            items=(TodoItem(content="Write tests", status=TodoStatus.PENDING),),
        )
        listed = await service.list_for_session_async("session-1")
        cleared = await service.clear_for_run_async(
            run_id="run-1",
            session_id="session-1",
        )
        await service.delete_for_run_async("run-2")
        after_run_delete = await service.list_for_session_async("session-1")
        await service.delete_for_session_async("session-1")
        after_session_delete = await service.list_for_session_async("session-1")
    finally:
        await event_log.close_async()

    assert missing.items == ()
    assert {snapshot.run_id for snapshot in listed} == {first.run_id, second.run_id}
    assert cleared.items == ()
    assert [snapshot.run_id for snapshot in after_run_delete] == ["run-1"]
    assert after_session_delete == ()


@pytest.mark.asyncio
async def test_async_todo_service_clear_allows_missing_event_hub(
    tmp_path: Path,
) -> None:
    service = TodoService(
        repository=TodoRepository(tmp_path / "todo-async-no-hub.db"),
        run_event_hub=None,
    )

    snapshot = await service.clear_for_run_async(
        run_id="run-1",
        session_id="session-1",
    )

    assert snapshot.items == ()
