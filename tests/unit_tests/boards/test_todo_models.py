from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_teams.boards.todo_models import (
    BoardTodoItem,
    BoardTodoSourceProvider,
    BoardTodoSourceType,
)


def test_board_todo_item_rejects_invalid_source_shapes() -> None:
    with pytest.raises(ValidationError, match="manual source type"):
        BoardTodoItem(
            todo_id="todo_1",
            workspace_id="workspace",
            title="Local issue",
            source_provider=BoardTodoSourceProvider.LOCAL,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="local:todo_1",
        )

    with pytest.raises(ValidationError, match="require repository_full_name"):
        BoardTodoItem(
            todo_id="todo_2",
            workspace_id="workspace",
            title="GitHub issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:1",
            issue_number=1,
        )

    with pytest.raises(ValidationError, match="require issue_number"):
        BoardTodoItem(
            todo_id="todo_3",
            workspace_id="workspace",
            title="GitHub issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:1",
            repository_full_name="owner/repo",
        )

    with pytest.raises(ValidationError, match="require pull_request_number"):
        BoardTodoItem(
            todo_id="todo_4",
            workspace_id="workspace",
            title="GitHub pull request",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_PULL_REQUEST,
            source_key="github:owner/repo:pr:1",
            repository_full_name="owner/repo",
        )
