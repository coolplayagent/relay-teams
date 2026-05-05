# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.board.adapter import (
    BoardEventKind,
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
    TaskBoardConfig,
    TaskBoardStateMap,
)
from relay_teams.agents.tasks.enums import TaskStatus


# ---------------------------------------------------------------------------
# BoardTaskState
# ---------------------------------------------------------------------------


class TestBoardTaskState:
    def test_all_states_exist(self) -> None:
        expected = {
            "backlog",
            "ready",
            "in_progress",
            "in_review",
            "blocked",
            "completed",
            "cancelled",
        }
        actual = {s.value for s in tuple(BoardTaskState)}
        assert actual == expected

    def test_string_comparison(self) -> None:
        assert BoardTaskState.BACKLOG == "backlog"
        assert BoardTaskState.COMPLETED == "completed"


# ---------------------------------------------------------------------------
# BoardEventKind
# ---------------------------------------------------------------------------


class TestBoardEventKind:
    def test_all_kinds_exist(self) -> None:
        expected = {
            "task_created",
            "task_moved",
            "task_assigned",
            "task_updated",
            "task_commented",
        }
        actual = {k.value for k in BoardEventKind}
        assert actual == expected


# ---------------------------------------------------------------------------
# BoardTask
# ---------------------------------------------------------------------------


class TestBoardTaskModel:
    def test_board_task_creation(self) -> None:
        task = BoardTask(
            board_task_id="ISSUE-1",
            title="Fix bug",
            description="A critical bug",
            state=BoardTaskState.READY,
        )
        assert task.board_task_id == "ISSUE-1"
        assert task.state == BoardTaskState.READY
        assert task.labels == ()
        assert task.raw_payload == {}

    def test_board_task_empty_id_raises(self) -> None:
        with pytest.raises(ValueError):
            BoardTask(
                board_task_id="",
                title="x",
                description="",
                state=BoardTaskState.BACKLOG,
            )

    def test_board_task_with_all_fields(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)
        task = BoardTask(
            board_task_id="42",
            title="Feature",
            description="desc",
            state=BoardTaskState.IN_PROGRESS,
            assignee="alice",
            labels=("bug", "urgent"),
            source_url="https://github.com/org/repo/issues/42",
            created_at=now,
            updated_at=now,
            raw_payload={"number": 42},
        )
        assert task.assignee == "alice"
        assert task.labels == ("bug", "urgent")
        assert task.raw_payload == {"number": 42}


# ---------------------------------------------------------------------------
# TaskBoardStateMap
# ---------------------------------------------------------------------------


class TestTaskBoardStateMap:
    def _default_map(self) -> TaskBoardStateMap:
        return TaskBoardStateMap()

    def test_state_map_bidirectional(self) -> None:
        sm = self._default_map()
        for status, board_state in sm.task_status_to_board.items():
            mapped_back = sm.board_state_to_task_status.get(board_state)
            assert mapped_back is not None
            assert status in mapped_back

    def test_all_task_statuses_mapped(self) -> None:
        sm = self._default_map()
        for status in tuple(TaskStatus):
            assert status in sm.task_status_to_board

    def test_all_board_states_mapped(self) -> None:
        sm = self._default_map()
        for state in tuple(BoardTaskState):
            assert state in sm.board_state_to_task_status

    def test_created_maps_to_backlog(self) -> None:
        sm = self._default_map()
        assert sm.task_status_to_board[TaskStatus.CREATED] == BoardTaskState.BACKLOG

    def test_completed_maps_to_completed(self) -> None:
        sm = self._default_map()
        assert sm.task_status_to_board[TaskStatus.COMPLETED] == BoardTaskState.COMPLETED

    def test_failed_maps_to_cancelled(self) -> None:
        sm = self._default_map()
        assert sm.task_status_to_board[TaskStatus.FAILED] == BoardTaskState.CANCELLED

    def test_blocked_maps_multiple_statuses(self) -> None:
        sm = self._default_map()
        blocked_statuses = sm.board_state_to_task_status[BoardTaskState.BLOCKED]
        assert TaskStatus.STOPPED in blocked_statuses
        assert TaskStatus.TIMEOUT in blocked_statuses


# ---------------------------------------------------------------------------
# TaskBoardConfig
# ---------------------------------------------------------------------------


class TestTaskBoardConfig:
    def test_internal_config(self) -> None:
        cfg = TaskBoardConfig(
            board_id="board-1",
            adapter="internal",
        )
        assert cfg.adapter == "internal"
        assert cfg.poll_interval_seconds == 30
        assert cfg.stall_timeout_seconds == 600

    def test_github_config(self) -> None:
        cfg = TaskBoardConfig(
            board_id="gh-board",
            adapter="github",
            github_repo="org/repo",
            github_token_env="GITHUB_TOKEN",
        )
        assert cfg.github_repo == "org/repo"

    def test_linear_config(self) -> None:
        cfg = TaskBoardConfig(
            board_id="lin-board",
            adapter="linear",
            linear_api_key_env="LINEAR_API_KEY",
            linear_team_id="TEAM-1",
        )
        assert cfg.linear_team_id == "TEAM-1"

    def test_empty_board_id_raises(self) -> None:
        with pytest.raises(ValueError):
            TaskBoardConfig(board_id="", adapter="internal")

    def test_poll_interval_minimum(self) -> None:
        with pytest.raises(ValueError):
            TaskBoardConfig(
                board_id="b",
                adapter="internal",
                poll_interval_seconds=1,
            )

    def test_custom_intervals(self) -> None:
        cfg = TaskBoardConfig(
            board_id="b",
            adapter="internal",
            poll_interval_seconds=10,
            stall_timeout_seconds=120,
        )
        assert cfg.poll_interval_seconds == 10
        assert cfg.stall_timeout_seconds == 120


# ---------------------------------------------------------------------------
# TaskBoardAdapter ABC
# ---------------------------------------------------------------------------


class TestTaskBoardAdapterABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            TaskBoardAdapter()  # type: ignore[abstract]

    def test_subclass_must_implement_all(self) -> None:
        class Partial(TaskBoardAdapter):
            async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
                return ()

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]
