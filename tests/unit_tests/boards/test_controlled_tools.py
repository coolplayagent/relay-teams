# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from relay_teams.boards.adapter import TaskBoardAdapter
from relay_teams.boards.controlled_tools import (
    board_add_comment,
    board_attach_evidence,
    board_link_pr,
    board_update_task,
    get_board_adapter,
    set_board_adapter,
)


def _make_mock_adapter() -> AsyncMock:
    """Build an AsyncMock that satisfies the TaskBoardAdapter protocol."""
    adapter: AsyncMock = AsyncMock(spec=TaskBoardAdapter)
    return adapter


@pytest.fixture
def mock_adapter() -> AsyncMock:
    return _make_mock_adapter()


@pytest.fixture(autouse=True)
def _setup_adapter(mock_adapter: AsyncMock) -> None:
    set_board_adapter(mock_adapter)


class TestControlledTools:
    @pytest.mark.asyncio
    async def test_board_add_comment(self, mock_adapter: AsyncMock) -> None:
        result = await board_add_comment(task_id="t-1", body="hello world")
        assert result["commented"] is True
        mock_adapter.add_comment.assert_called_once_with(
            task_id="t-1", body="hello world"
        )

    @pytest.mark.asyncio
    async def test_board_add_comment_empty_body(self, mock_adapter: AsyncMock) -> None:
        result = await board_add_comment(task_id="t-1", body="")
        assert result["commented"] is True

    @pytest.mark.asyncio
    async def test_board_update_task_state(self, mock_adapter: AsyncMock) -> None:
        result = await board_update_task(
            task_id="t-1",
            state="completed",
        )
        assert result["updated"] is True
        mock_adapter.move_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_board_update_task_labels_only(self, mock_adapter: AsyncMock) -> None:
        result = await board_update_task(
            task_id="t-1",
            labels=("bug", "urgent"),
        )
        assert result["updated"] is True
        mock_adapter.move_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_board_update_task_no_fields(self, mock_adapter: AsyncMock) -> None:
        result = await board_update_task(task_id="t-1")
        assert result["updated"] is True
        mock_adapter.move_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_board_link_pr(self, mock_adapter: AsyncMock) -> None:
        result = await board_link_pr(
            task_id="t-1",
            pr_url="https://github.com/org/repo/pull/42",
        )
        assert result["linked"] is True
        mock_adapter.add_artifact.assert_called_once_with(
            task_id="t-1",
            name="Pull Request",
            url="https://github.com/org/repo/pull/42",
        )

    @pytest.mark.asyncio
    async def test_board_attach_evidence(self, mock_adapter: AsyncMock) -> None:
        result = await board_attach_evidence(
            task_id="t-1",
            evidence_type="CI Result",
            content="All tests passed",
        )
        assert result["attached"] is True
        mock_adapter.add_artifact.assert_called_once()

    @pytest.mark.asyncio
    async def test_board_update_task_state_and_labels(
        self, mock_adapter: AsyncMock
    ) -> None:
        result = await board_update_task(
            task_id="t-1",
            state="in_progress",
            labels=("wip",),
        )
        assert result["updated"] is True
        mock_adapter.move_task.assert_called_once()

    def test_set_board_adapter_none_raises(self) -> None:
        set_board_adapter(None)
        with pytest.raises(RuntimeError, match="No board adapter"):
            get_board_adapter()
