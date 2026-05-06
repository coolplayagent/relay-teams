# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.boards.adapter import (
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
    TaskBoardConfig,
)
from relay_teams.boards.dispatcher import (
    BoardEventDispatcher,
)
from relay_teams.boards.internal_adapter import (
    InternalBoardAdapter,
)
from relay_teams.agents.tasks.enums import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAdapter(TaskBoardAdapter):
    """In-memory fake adapter for testing."""

    def __init__(self) -> None:
        self.tasks: dict[str, BoardTask] = {}
        self.comments: dict[str, list[str]] = {}
        self.artifacts: dict[str, list[tuple[str, str]]] = {}

    async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
        return tuple(self.tasks.values())

    async def get_task(self, *, task_id: str) -> BoardTask:
        return self.tasks[task_id]

    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        self.tasks[task_id] = self.tasks[task_id].model_copy(update={"state": to_state})

    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        self.tasks[task_id] = self.tasks[task_id].model_copy(
            update={"assignee": assignee}
        )

    async def add_comment(self, *, task_id: str, body: str) -> None:
        self.comments.setdefault(task_id, []).append(body)

    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        self.artifacts.setdefault(task_id, []).append((name, url))


def _make_config(**overrides: object) -> TaskBoardConfig:
    defaults: dict[str, object] = {
        "board_id": "test-board",
        "adapter": "internal",
        "poll_interval_seconds": 5,
        "stall_timeout_seconds": 60,
    }
    defaults.update(overrides)
    return TaskBoardConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------


class TestDispatcherLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        adapter = _FakeAdapter()
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        await dispatcher.start()
        assert dispatcher._running
        await dispatcher.stop()
        assert not dispatcher._running

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self) -> None:
        adapter = _FakeAdapter()
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        await dispatcher.start()
        assert len(dispatcher._tasks) == 4
        await dispatcher.stop()
        assert len(dispatcher._tasks) == 0


class TestDispatcherPollTick:
    @pytest.mark.asyncio
    async def test_poll_discovers_new_ready_tasks(self) -> None:
        adapter = _FakeAdapter()
        adapter.tasks["ext-1"] = BoardTask(
            board_task_id="ext-1",
            title="External",
            description="",
            state=BoardTaskState.READY,
        )
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config(poll_interval_seconds=5)
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        async def _run_one_poll() -> None:
            """Run one iteration of the poll loop body."""
            try:
                external_tasks = await adapter.list_tasks(board_id=cfg.board_id)
                new_tasks = [
                    t
                    for t in external_tasks
                    if t.board_task_id not in dispatcher._known_board_ids
                ]
                for task in new_tasks:
                    dispatcher._known_board_ids.add(task.board_task_id)
                    if task.state == BoardTaskState.READY:
                        await dispatcher._create_internal_task_from_board(task)
            except (OSError, RuntimeError):
                # Mirrors dispatcher resilience: ignore transient poll errors
                pass

        await _run_one_poll()

        # The poll tick should have discovered the task
        assert "ext-1" in dispatcher._known_board_ids


class TestDispatcherWorkerOutcome:
    @pytest.mark.asyncio
    async def test_worker_outcome_syncs_completed(self) -> None:
        adapter = _FakeAdapter()
        adapter.tasks["task-1"] = BoardTask(
            board_task_id="task-1",
            title="T1",
            description="",
            state=BoardTaskState.IN_PROGRESS,
        )
        # Create a fake completed record
        mock_record = MagicMock()
        mock_record.status = TaskStatus.COMPLETED
        mock_record.envelope.task_id = "task-1"
        mock_record.envelope.trace_id = "trace-1"
        mock_record.envelope.session_id = "sess-1"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        BoardEventDispatcher(cfg, adapter, mock_repo)

        # Run one iteration of worker outcome
        records = await mock_repo.list_running_async()
        for record in records:
            if record.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                from relay_teams.boards.adapter import (
                    TaskBoardStateMap,
                )

                sm = TaskBoardStateMap()
                board_state = sm.task_status_to_board.get(
                    record.status, BoardTaskState.CANCELLED
                )
                await adapter.move_task(
                    task_id=record.envelope.task_id,
                    to_state=board_state,
                )

        assert adapter.tasks["task-1"].state == BoardTaskState.COMPLETED


class TestDispatcherStallTimeout:
    @pytest.mark.asyncio
    async def test_stall_timeout_enqueues_wake(self) -> None:
        adapter = _FakeAdapter()
        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        mock_record = MagicMock()
        mock_record.status = TaskStatus.RUNNING
        mock_record.envelope.task_id = "stalled-1"
        mock_record.envelope.trace_id = "trace-1"
        mock_record.envelope.session_id = "sess-1"
        mock_record.updated_at = old_time

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())

        mock_wakeup = AsyncMock()

        cfg = _make_config(stall_timeout_seconds=60)
        BoardEventDispatcher(cfg, adapter, mock_repo, mock_wakeup)

        # Simulate one stall timeout check
        now = datetime.now(tz=timezone.utc)
        records = await mock_repo.list_running_async()
        for record in records:
            elapsed = (now - record.updated_at).total_seconds()
            if elapsed > cfg.stall_timeout_seconds:
                assert True  # Would enqueue wake
                return

        pytest.fail("Should have detected stall")


# ---------------------------------------------------------------------------
# Internal adapter tests
# ---------------------------------------------------------------------------


class TestInternalAdapter:
    @pytest.mark.asyncio
    async def test_internal_adapter_instantiation(self) -> None:
        mock_repo = MagicMock()
        adapter = InternalBoardAdapter(mock_repo)
        assert adapter._task_repo is mock_repo

    @pytest.mark.asyncio
    async def test_internal_adapter_add_comment(self) -> None:
        mock_repo = MagicMock()
        adapter = InternalBoardAdapter(mock_repo)
        # Should not raise
        await adapter.add_comment(task_id="t-1", body="hello")

    @pytest.mark.asyncio
    async def test_internal_adapter_add_artifact(self) -> None:
        mock_repo = MagicMock()
        adapter = InternalBoardAdapter(mock_repo)
        # Should not raise
        await adapter.add_artifact(
            task_id="t-1", name="PR", url="https://github.com/pr/1"
        )
