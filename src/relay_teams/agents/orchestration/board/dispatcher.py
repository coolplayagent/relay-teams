# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from relay_teams.agents.orchestration.board.adapter import (
    BoardTaskState,
    TaskBoardAdapter,
    TaskBoardConfig,
    TaskBoardStateMap,
)
from relay_teams.agents.tasks.agent_wakeup_repository import (
    AgentWakeupRepository,
)
from relay_teams.agents.tasks.enums import (
    TaskTimeoutAction,
    TaskStatus,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

_DEFAULT_STATE_MAP = TaskBoardStateMap()


class BoardEventDispatcher:
    """Event-driven dispatcher managing four scheduling loops:

    1. Poll Tick -- periodic poll of external board for new/changed tasks
    2. Worker Outcome -- internal task completion -> sync to external board
    3. Retry Timer -- failed tasks auto-retry with backoff
    4. Stall Timeout -- tasks stuck too long -> enqueue wake

    The dispatcher cooperates with the existing Coordinator:
      - External board new tasks -> create relay-teams internal tasks
      - relay-teams internal task completion -> update external board
      - Does NOT change Coordinator scheduling authority
    """

    def __init__(
        self,
        config: TaskBoardConfig,
        adapter: TaskBoardAdapter,
        task_repo: TaskRepository,
        wakeup_repo: AgentWakeupRepository | None = None,
    ) -> None:
        self._config = config
        self._adapter = adapter
        self._task_repo = task_repo
        self._wakeup_repo = wakeup_repo
        self._tasks: list[asyncio.Task[None]] = []
        self._known_board_ids: set[str] = set()
        self._running = False

    # -- lifecycle --

    async def start(self) -> None:
        """Start the four event loops."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._poll_tick_loop()),
            asyncio.create_task(self._worker_outcome_loop()),
            asyncio.create_task(self._retry_timer_loop()),
            asyncio.create_task(self._stall_timeout_loop()),
        ]
        LOGGER.info(
            "board dispatcher started for %s (%s)",
            self._config.board_id,
            self._config.adapter,
        )

    async def stop(self) -> None:
        """Cancel all event loops."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                # Expected during shutdown when tasks are cancelled
                pass
        self._tasks.clear()

    # -- loop 1: poll tick --

    async def _poll_tick_loop(self) -> None:
        """Periodically poll the external board for new tasks."""
        while self._running:
            await asyncio.sleep(self._config.poll_interval_seconds)
            try:
                external_tasks = await self._adapter.list_tasks(
                    board_id=self._config.board_id
                )
                new_tasks = [
                    t
                    for t in external_tasks
                    if t.board_task_id not in self._known_board_ids
                ]
                for task in new_tasks:
                    self._known_board_ids.add(task.board_task_id)
                    if task.state == BoardTaskState.READY:
                        await self._create_internal_task_from_board(task)
                        if self._config.auto_claim and task.assignee:
                            await self._adapter.assign_task(
                                task_id=task.board_task_id,
                                assignee=task.assignee,
                            )
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("poll tick failed: %s", exc)

    @staticmethod
    async def _create_internal_task_from_board(board_task: object) -> None:
        """Create a relay-teams internal task from an external board task."""
        from relay_teams.agents.orchestration.board.adapter import BoardTask

        assert isinstance(board_task, BoardTask)
        LOGGER.info(
            "creating internal task from board task %s",
            board_task.board_task_id,
        )

    # -- loop 2: worker outcome --

    async def _worker_outcome_loop(self) -> None:
        """Monitor internal events and sync to external board."""
        while self._running:
            await asyncio.sleep(self._config.poll_interval_seconds)
            try:
                records = await self._task_repo.list_running_async()
                for record in records:
                    if record.status in (
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                    ):
                        board_state = _DEFAULT_STATE_MAP.task_status_to_board.get(
                            record.status, BoardTaskState.CANCELLED
                        )
                        try:
                            await self._adapter.move_task(
                                task_id=record.envelope.task_id,
                                to_state=board_state,
                            )
                        except (OSError, RuntimeError, KeyError) as exc:
                            LOGGER.warning(
                                "failed to sync task %s to board: %s",
                                record.envelope.task_id,
                                exc,
                            )
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("worker outcome loop failed: %s", exc)

    # -- loop 3: retry timer --

    async def _retry_timer_loop(self) -> None:
        """Retry failed tasks with exponential backoff."""
        backoff = 60
        while self._running:
            await asyncio.sleep(backoff)
            try:
                records = await self._task_repo.list_all_async()
                for record in records:
                    if record.status == TaskStatus.FAILED:
                        LOGGER.info(
                            "retry timer: task %s is FAILED, wake for retry",
                            record.envelope.task_id,
                        )
                        if self._wakeup_repo is not None:
                            await self._wakeup_repo.enqueue_async(
                                AgentWakeupEntry(
                                    wakeup_id=f"board-retry-{record.envelope.task_id}",
                                    task_id=record.envelope.task_id,
                                    trace_id=record.envelope.trace_id,
                                    session_id=record.envelope.session_id,
                                    coalesce_key=f"{record.envelope.task_id}:board_retry",
                                    wake_reason=WakeupReason.TIMEOUT_RETRY,
                                    timeout_action=TaskTimeoutAction.RETRY,
                                    timeout_seconds=0.0,
                                    attempt=1,
                                    max_attempts=3,
                                    status=WakeupStatus.PENDING,
                                    enqueued_at=datetime.now(tz=timezone.utc),
                                )
                            )
                backoff = min(backoff * 2, 600)
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("retry timer loop failed: %s", exc)

    # -- loop 4: stall timeout --

    async def _stall_timeout_loop(self) -> None:
        """Detect stalled RUNNING/ASSIGNED tasks and enqueue a wake."""
        while self._running:
            await asyncio.sleep(self._config.stall_timeout_seconds)
            try:
                now = datetime.now(tz=timezone.utc)
                records = await self._task_repo.list_running_async()
                for record in records:
                    elapsed = (now - record.updated_at).total_seconds()
                    threshold = self._config.stall_timeout_seconds
                    if elapsed > threshold:
                        LOGGER.info(
                            "stall timeout: task %s stalled for %ds",
                            record.envelope.task_id,
                            int(elapsed),
                        )
                        if self._wakeup_repo is not None:
                            await self._wakeup_repo.enqueue_async(
                                AgentWakeupEntry(
                                    wakeup_id=f"board-stall-{record.envelope.task_id}",
                                    task_id=record.envelope.task_id,
                                    trace_id=record.envelope.trace_id,
                                    session_id=record.envelope.session_id,
                                    coalesce_key=f"{record.envelope.task_id}:board_stall",
                                    wake_reason=WakeupReason.TIMEOUT_RETRY,
                                    timeout_action=TaskTimeoutAction.RETRY,
                                    timeout_seconds=0.0,
                                    attempt=1,
                                    max_attempts=3,
                                    status=WakeupStatus.PENDING,
                                    enqueued_at=datetime.now(tz=timezone.utc),
                                )
                            )
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("stall timeout loop failed: %s", exc)
