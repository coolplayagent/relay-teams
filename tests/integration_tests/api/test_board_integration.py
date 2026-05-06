# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.boards.adapter import (
    BoardTaskState,
    TaskBoardStateMap,
)
from relay_teams.agents.tasks.enums import TaskStatus


class TestBoardStateMapIntegration:
    """Integration: State map round-trip consistency."""

    def test_every_task_status_round_trips(self) -> None:
        sm = TaskBoardStateMap()
        for status in tuple(TaskStatus):
            board_state = sm.task_status_to_board[status]
            back = sm.board_state_to_task_status[board_state]
            assert status in back

    def test_completed_is_terminal(self) -> None:
        sm = TaskBoardStateMap()
        board = sm.task_status_to_board[TaskStatus.COMPLETED]
        assert board == BoardTaskState.COMPLETED
