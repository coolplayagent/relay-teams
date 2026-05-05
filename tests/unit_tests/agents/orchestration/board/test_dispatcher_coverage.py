# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay_teams.agents.orchestration.board.adapter import (
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
    TaskBoardConfig,
)
from relay_teams.agents.orchestration.board.dispatcher import (
    BoardEventDispatcher,
)
from relay_teams.agents.tasks.enums import TaskStatus


class _FakeAdapter(TaskBoardAdapter):
    def __init__(self) -> None:
        self.tasks: dict[str, BoardTask] = {}

    async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
        return tuple(self.tasks.values())

    async def get_task(self, *, task_id: str) -> BoardTask:
        return self.tasks[task_id]

    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        self.tasks[task_id] = self.tasks[task_id].model_copy(update={"state": to_state})

    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        pass

    async def add_comment(self, *, task_id: str, body: str) -> None:
        pass

    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        pass


def _make_config(**overrides: object) -> TaskBoardConfig:
    defaults: dict[str, object] = {
        "board_id": "test-board",
        "adapter": "internal",
        "poll_interval_seconds": 5,
        "stall_timeout_seconds": 60,
    }
    defaults.update(overrides)
    return TaskBoardConfig(**defaults)  # type: ignore[arg-type]


class TestDispatcherMethods:
    @pytest.mark.asyncio
    async def test_create_internal_task_from_board(self) -> None:
        adapter = _FakeAdapter()
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        board_task = BoardTask(
            board_task_id="ext-1",
            title="External task",
            description="Do something",
            state=BoardTaskState.READY,
        )
        dispatcher._known_board_ids.add(board_task.board_task_id)
        await dispatcher._create_internal_task_from_board(board_task)
        assert "ext-1" in dispatcher._known_board_ids

    @pytest.mark.asyncio
    async def test_worker_outcome_syncs_running_task(self) -> None:
        adapter = _FakeAdapter()
        mock_record = MagicMock()
        mock_record.status = TaskStatus.RUNNING
        mock_record.envelope.task_id = "t-1"
        mock_record.envelope.trace_id = "tr-1"
        mock_record.envelope.session_id = "s-1"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        await dispatcher.start()
        await asyncio.sleep(0.1)
        await dispatcher.stop()
        assert not dispatcher._running

    @pytest.mark.asyncio
    async def test_stop_without_start(self) -> None:
        adapter = _FakeAdapter()
        mock_repo = MagicMock()
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        await dispatcher.stop()
        assert not dispatcher._running

    @pytest.mark.asyncio
    async def test_poll_tick_discovers_new_task(self) -> None:
        adapter = _FakeAdapter()
        board_task = BoardTask(
            board_task_id="ext-2",
            title="New task",
            description="desc",
            state=BoardTaskState.READY,
            assignee="dev1",
        )
        adapter.tasks["ext-2"] = board_task
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._poll_tick_loop()
        assert "ext-2" in dispatcher._known_board_ids

    @pytest.mark.asyncio
    async def test_poll_tick_ignores_known_tasks(self) -> None:
        adapter = _FakeAdapter()
        board_task = BoardTask(
            board_task_id="ext-3",
            title="Known task",
            description="desc",
            state=BoardTaskState.READY,
        )
        adapter.tasks["ext-3"] = board_task
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True
        dispatcher._known_board_ids.add("ext-3")

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._poll_tick_loop()

    @pytest.mark.asyncio
    async def test_worker_outcome_moves_completed_task(self) -> None:
        adapter = _FakeAdapter()
        bt = BoardTask(
            board_task_id="ext-4",
            title="Done task",
            state=BoardTaskState.IN_PROGRESS,
        )
        adapter.tasks["ext-4"] = bt
        mock_record = MagicMock()
        mock_record.status = TaskStatus.COMPLETED
        mock_record.envelope.task_id = "ext-4"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._worker_outcome_loop()

    @pytest.mark.asyncio
    async def test_worker_outcome_moves_failed_task(self) -> None:
        adapter = _FakeAdapter()
        bt = BoardTask(
            board_task_id="ext-5",
            title="Failed task",
            state=BoardTaskState.IN_PROGRESS,
        )
        adapter.tasks["ext-5"] = bt
        mock_record = MagicMock()
        mock_record.status = TaskStatus.FAILED
        mock_record.envelope.task_id = "ext-5"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._worker_outcome_loop()

    @pytest.mark.asyncio
    async def test_retry_timer_with_failed_task(self) -> None:
        adapter = _FakeAdapter()
        mock_record = MagicMock()
        mock_record.status = TaskStatus.FAILED
        mock_record.envelope.task_id = "t-fail"
        mock_record.envelope.trace_id = "tr-1"
        mock_record.envelope.session_id = "s-1"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=[mock_record])

        mock_wakeup = MagicMock()
        mock_wakeup.enqueue_async = AsyncMock()

        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo, mock_wakeup)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._retry_timer_loop()
        mock_wakeup.enqueue_async.assert_called()

    @pytest.mark.asyncio
    async def test_stall_timeout_with_stalled_task(self) -> None:
        adapter = _FakeAdapter()
        mock_record = MagicMock()
        mock_record.status = TaskStatus.RUNNING
        mock_record.envelope.task_id = "t-stall"
        mock_record.envelope.trace_id = "tr-2"
        mock_record.envelope.session_id = "s-2"
        mock_record.updated_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())

        mock_wakeup = MagicMock()
        mock_wakeup.enqueue_async = AsyncMock()

        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo, mock_wakeup)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._stall_timeout_loop()
        mock_wakeup.enqueue_async.assert_called()

    @pytest.mark.asyncio
    async def test_poll_tick_handles_error(self) -> None:
        adapter = _FakeAdapter()
        adapter.list_tasks = AsyncMock(side_effect=OSError("network error"))
        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._poll_tick_loop()

    @pytest.mark.asyncio
    async def test_worker_outcome_handles_move_error(self) -> None:
        adapter = _FakeAdapter()
        adapter.move_task = AsyncMock(side_effect=OSError("api error"))
        mock_record = MagicMock()
        mock_record.status = TaskStatus.COMPLETED
        mock_record.envelope.task_id = "ext-6"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())
        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._worker_outcome_loop()

    @pytest.mark.asyncio
    async def test_retry_timer_no_wakeup_repo(self) -> None:
        adapter = _FakeAdapter()
        mock_record = MagicMock()
        mock_record.status = TaskStatus.FAILED
        mock_record.envelope.task_id = "t-fail2"

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=())
        mock_repo.list_all_async = AsyncMock(return_value=[mock_record])

        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo, None)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._retry_timer_loop()

    @pytest.mark.asyncio
    async def test_stall_timeout_no_wakeup_repo(self) -> None:
        adapter = _FakeAdapter()
        mock_record = MagicMock()
        mock_record.status = TaskStatus.RUNNING
        mock_record.envelope.task_id = "t-stall2"
        mock_record.updated_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)

        mock_repo = MagicMock()
        mock_repo.list_running_async = AsyncMock(return_value=[mock_record])
        mock_repo.list_all_async = AsyncMock(return_value=())

        cfg = _make_config()
        dispatcher = BoardEventDispatcher(cfg, adapter, mock_repo, None)
        dispatcher._running = True

        call_count = 0

        async def _sleep_then_stop(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                dispatcher._running = False

        with patch("asyncio.sleep", side_effect=_sleep_then_stop):
            await dispatcher._stall_timeout_loop()
