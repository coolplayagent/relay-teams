# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.agents.orchestration.board.adapter import (
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
    TaskBoardStateMap,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

_DEFAULT_STATE_MAP = TaskBoardStateMap()


def _task_status_to_board(status: TaskStatus) -> BoardTaskState:
    return _DEFAULT_STATE_MAP.task_status_to_board.get(status, BoardTaskState.BACKLOG)


def _board_to_task_status(state: BoardTaskState) -> TaskStatus:
    statuses = _DEFAULT_STATE_MAP.board_state_to_task_status.get(state)
    if statuses:
        return statuses[0]
    return TaskStatus.CREATED


def _record_to_board_task(record: object) -> BoardTask:
    from relay_teams.agents.tasks.models import TaskRecord

    assert isinstance(record, TaskRecord)
    envelope = record.envelope
    return BoardTask(
        board_task_id=envelope.task_id,
        title=envelope.title or envelope.objective[:80],
        description=envelope.objective,
        state=_task_status_to_board(record.status),
        assignee=record.assigned_instance_id,
        labels=(envelope.role_id,) if envelope.role_id else (),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class InternalBoardAdapter(TaskBoardAdapter):
    """Internal board adapter -- treats relay-teams tasks as the board.

    Suitable when no external tracker is needed. Wraps the existing
    TaskRepository without changing any behaviour.
    """

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo

    async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
        records = await self._task_repo.list_by_trace_async(board_id)
        if not records:
            records = await self._task_repo.list_by_session_async(board_id)
        return tuple(_record_to_board_task(r) for r in records)

    async def get_task(self, *, task_id: str) -> BoardTask:
        record = await self._task_repo.get_async(task_id)
        return _record_to_board_task(record)

    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        target_status = _board_to_task_status(to_state)
        await self._task_repo.update_status_async(task_id, target_status)

    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        record = await self._task_repo.get_async(task_id)
        record.assigned_instance_id = assignee
        record.updated_at = datetime.now(tz=timezone.utc)

    async def add_comment(self, *, task_id: str, body: str) -> None:
        LOGGER.info("board comment on %s: %s", task_id, body[:200])

    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        LOGGER.info("board artifact on %s: %s (%s)", task_id, name, url)
