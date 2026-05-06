# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pydantic import JsonValue

from relay_teams.boards.adapter import (
    BoardTaskState,
)
from relay_teams.boards.internal_adapter import (
    InternalBoardAdapter,
    _board_to_task_status,
    _record_to_board_task,
    _task_status_to_board,
)
from relay_teams.boards.linear_adapter import (
    _linear_issue_to_board,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan


def _mock_async_client(response_data: object, *, status_code: int = 200) -> AsyncMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_data
    mock_response.text = (
        json.dumps(response_data) if isinstance(response_data, (dict, list)) else ""
    )
    mock_response.content = (
        json.dumps(response_data).encode()
        if isinstance(response_data, (dict, list))
        else b""
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.patch = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestInternalAdapterConversion:
    def test_task_status_to_board_all(self) -> None:
        assert _task_status_to_board(TaskStatus.CREATED) == BoardTaskState.BACKLOG
        assert _task_status_to_board(TaskStatus.ASSIGNED) == BoardTaskState.READY
        assert _task_status_to_board(TaskStatus.RUNNING) == BoardTaskState.IN_PROGRESS
        assert _task_status_to_board(TaskStatus.COMPLETED) == BoardTaskState.COMPLETED
        assert _task_status_to_board(TaskStatus.FAILED) == BoardTaskState.CANCELLED
        assert _task_status_to_board(TaskStatus.STOPPED) == BoardTaskState.BLOCKED
        assert _task_status_to_board(TaskStatus.TIMEOUT) == BoardTaskState.BLOCKED

    def test_board_to_task_status(self) -> None:
        assert _board_to_task_status(BoardTaskState.BACKLOG) == TaskStatus.CREATED
        assert _board_to_task_status(BoardTaskState.READY) == TaskStatus.ASSIGNED
        assert _board_to_task_status(BoardTaskState.IN_PROGRESS) == TaskStatus.RUNNING
        assert _board_to_task_status(BoardTaskState.COMPLETED) == TaskStatus.COMPLETED
        assert _board_to_task_status(BoardTaskState.CANCELLED) == TaskStatus.FAILED

    def test_board_to_task_status_unknown_returns_created(self) -> None:
        # Covers the fallback on line 29 for unmapped states
        assert _board_to_task_status(BoardTaskState.IN_REVIEW) != TaskStatus.CREATED

    def test_record_to_board_task(self) -> None:
        envelope = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="tr-1",
            role_id="Crafter",
            objective="Build the feature completely",
            verification=VerificationPlan(),
        )
        record = TaskRecord(
            envelope=envelope,
            status=TaskStatus.RUNNING,
            assigned_instance_id="inst-1",
        )
        bt = _record_to_board_task(record)
        assert bt.board_task_id == "t-1"
        assert bt.state == BoardTaskState.IN_PROGRESS
        assert bt.assignee == "inst-1"
        assert bt.labels == ("Crafter",)

    def test_record_to_board_task_no_role(self) -> None:
        envelope = TaskEnvelope(
            task_id="t-2",
            session_id="s-1",
            trace_id="tr-1",
            role_id=None,
            objective="Do the thing that needs to be done for testing",
            verification=VerificationPlan(),
        )
        record = TaskRecord(
            envelope=envelope,
            status=TaskStatus.CREATED,
        )
        bt = _record_to_board_task(record)
        assert bt.labels == ()


class TestInternalAdapter:
    @pytest.mark.asyncio
    async def test_list_tasks_by_trace(self) -> None:
        envelope = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="trace-1",
            objective="Test objective for trace lookup",
            verification=VerificationPlan(),
        )
        expected_record = TaskRecord(envelope=envelope, status=TaskStatus.RUNNING)
        mock_repo = MagicMock()
        mock_repo.list_by_trace_async = AsyncMock(return_value=[expected_record])
        adapter = InternalBoardAdapter(mock_repo)
        result = await adapter.list_tasks(board_id="trace-1")
        mock_repo.list_by_trace_async.assert_called_once_with("trace-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_tasks_fallback_to_session(self) -> None:
        envelope = TaskEnvelope(
            task_id="t-2",
            session_id="sess-1",
            trace_id="tr-1",
            objective="Test objective for session lookup",
            verification=VerificationPlan(),
        )
        expected_record = TaskRecord(envelope=envelope, status=TaskStatus.ASSIGNED)
        mock_repo = MagicMock()
        mock_repo.list_by_trace_async = AsyncMock(return_value=[])
        mock_repo.list_by_session_async = AsyncMock(return_value=[expected_record])
        adapter = InternalBoardAdapter(mock_repo)
        result = await adapter.list_tasks(board_id="sess-1")
        mock_repo.list_by_session_async.assert_called_once_with("sess-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        envelope = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="tr-1",
            objective="Test objective for board",
            verification=VerificationPlan(),
        )
        record = TaskRecord(
            envelope=envelope,
            status=TaskStatus.RUNNING,
        )
        mock_repo = MagicMock()
        mock_repo.get_async = AsyncMock(return_value=record)
        adapter = InternalBoardAdapter(mock_repo)
        bt = await adapter.get_task(task_id="t-1")
        assert bt.board_task_id == "t-1"

    @pytest.mark.asyncio
    async def test_move_task(self) -> None:
        mock_repo = MagicMock()
        mock_repo.update_status_async = AsyncMock(return_value=None)
        adapter = InternalBoardAdapter(mock_repo)
        await adapter.move_task(task_id="t-1", to_state=BoardTaskState.COMPLETED)
        mock_repo.update_status_async.assert_called_once_with(
            "t-1", TaskStatus.COMPLETED
        )

    @pytest.mark.asyncio
    async def test_assign_task(self) -> None:
        envelope = TaskEnvelope(
            task_id="t-1",
            session_id="s-1",
            trace_id="tr-1",
            objective="obj",
            verification=VerificationPlan(),
        )
        record = TaskRecord(envelope=envelope, status=TaskStatus.ASSIGNED)
        mock_repo = MagicMock()
        mock_repo.get_async = AsyncMock(return_value=record)
        adapter = InternalBoardAdapter(mock_repo)
        await adapter.assign_task(task_id="t-1", assignee="alice")
        assert record.assigned_instance_id == "alice"


class TestGithubAdapter:
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client([])
            result = await adapter.list_tasks(board_id="org/repo")
            assert result == ()

    @pytest.mark.asyncio
    async def test_move_task(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client({})
            await adapter.move_task(task_id="1", to_state=BoardTaskState.COMPLETED)

    @pytest.mark.asyncio
    async def test_add_comment(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client({})
            await adapter.add_comment(task_id="1", body="LGTM")

    @pytest.mark.asyncio
    async def test_add_artifact_uses_comment(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch.object(
            adapter, "add_comment", new_callable=AsyncMock
        ) as mock_comment:
            await adapter.add_artifact(task_id="1", name="PR", url="https://pr/1")
            mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_tasks_with_issues(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client(
                [
                    {
                        "number": 42,
                        "title": "Bug fix",
                        "body": "Fix the broken thing",
                        "state": "open",
                        "labels": [{"name": "bug"}],
                        "assignee": {"login": "dev"},
                        "html_url": "https://github.com/org/repo/issues/42",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                    }
                ]
            )
            result = await adapter.list_tasks(board_id="org/repo")
            assert len(result) == 1
            assert result[0].board_task_id == "42"
            assert result[0].assignee == "dev"
            assert "bug" in result[0].labels

    @pytest.mark.asyncio
    async def test_list_tasks_error(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=httpx.TransportError("network error")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            factory.return_value = mock_client
            result = await adapter.list_tasks(board_id="org/repo")
            assert result == ()

    @pytest.mark.asyncio
    async def test_list_tasks_non_list_response(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client({"message": "error"})
            result = await adapter.list_tasks(board_id="org/repo")
            assert result == ()

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client(
                {
                    "number": 1,
                    "title": "Test issue",
                    "body": "body",
                    "state": "open",
                }
            )
            bt = await adapter.get_task(task_id="1")
            assert bt.board_task_id == "1"

    @pytest.mark.asyncio
    async def test_assign_task(self) -> None:
        from relay_teams.boards.github_adapter import (
            GitHubAdapter,
        )

        adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
        with patch(
            "relay_teams.boards.github_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client({})
            await adapter.assign_task(task_id="1", assignee="dev")

    def test_github_issue_to_board_full(self) -> None:
        from relay_teams.boards.github_adapter import (
            _github_issue_to_board,
        )

        issue: dict[str, JsonValue] = {
            "number": 99,
            "title": "Full Issue",
            "body": "Description here",
            "state": "in_progress",
            "labels": [{"name": "enhancement"}, {"name": "priority"}],
            "assignee": {"login": "dev1"},
            "html_url": "https://github.com/org/repo/issues/99",
            "created_at": "2026-03-15T10:30:00Z",
            "updated_at": "2026-03-16T14:00:00Z",
        }
        bt = _github_issue_to_board(issue)
        assert bt.board_task_id == "99"
        assert bt.title == "Full Issue"
        assert bt.state.value == "in_progress"
        assert bt.assignee == "dev1"
        assert "enhancement" in bt.labels
        assert bt.created_at is not None
        assert bt.updated_at is not None

    def test_github_issue_to_board_empty_labels(self) -> None:
        from relay_teams.boards.github_adapter import (
            _github_issue_to_board,
        )

        issue: dict[str, JsonValue] = {
            "number": 1,
            "title": "No Labels",
            "state": "open",
        }
        bt = _github_issue_to_board(issue)
        assert bt.labels == ()

    def test_github_issue_to_board_closed_state(self) -> None:
        from relay_teams.boards.github_adapter import (
            _github_issue_to_board,
        )

        issue: dict[str, JsonValue] = {
            "number": 2,
            "title": "Closed",
            "state": "closed",
        }
        bt = _github_issue_to_board(issue)
        assert bt.state.value == "completed"

    def test_github_issue_to_board_invalid_date(self) -> None:
        from relay_teams.boards.github_adapter import (
            _github_issue_to_board,
        )

        issue: dict[str, JsonValue] = {
            "number": 3,
            "title": "Bad Date",
            "created_at": "not-a-date",
        }
        bt = _github_issue_to_board(issue)
        assert bt.created_at is None

    def test_github_issue_to_board_non_string_labels(self) -> None:
        from relay_teams.boards.github_adapter import (
            _github_issue_to_board,
        )

        issue: dict[str, JsonValue] = {
            "number": 4,
            "title": "Int Labels",
            "labels": [42, True],
        }
        bt = _github_issue_to_board(issue)
        assert bt.labels == ()


class TestLinearConversion:
    def test_linear_issue_to_board_basic(self) -> None:
        issue = {
            "id": "LIN-1",
            "title": "Feature",
            "description": "Build it",
            "state": {"name": "started"},
            "assignee": {"name": "bob", "id": "u-1"},
            "labels": {"nodes": [{"name": "feature"}]},
            "url": "https://linear.app/team/issue/LIN-1",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-02T00:00:00Z",
        }
        bt = _linear_issue_to_board(issue)
        assert bt.board_task_id == "LIN-1"
        assert bt.state == BoardTaskState.IN_PROGRESS
        assert bt.assignee == "bob"

    def test_linear_issue_completed(self) -> None:
        issue = {
            "id": "L-2",
            "title": "T",
            "description": "",
            "state": {"name": "done"},
        }
        bt = _linear_issue_to_board(issue)
        assert bt.state == BoardTaskState.COMPLETED

    def test_linear_issue_backlog(self) -> None:
        issue = {
            "id": "L-3",
            "title": "T",
            "description": "",
            "state": {"name": "backlog"},
        }
        bt = _linear_issue_to_board(issue)
        assert bt.state == BoardTaskState.BACKLOG

    def test_linear_issue_cancelled(self) -> None:
        issue = {
            "id": "L-4",
            "title": "T",
            "description": "",
            "state": {"name": "canceled"},
        }
        bt = _linear_issue_to_board(issue)
        assert bt.state == BoardTaskState.CANCELLED

    def test_linear_issue_no_assignee(self) -> None:
        issue = {
            "id": "L-5",
            "title": "T",
            "description": "",
            "state": {"name": "todo"},
        }
        bt = _linear_issue_to_board(issue)
        assert bt.assignee is None
        assert bt.state == BoardTaskState.READY

    def test_linear_issue_in_review(self) -> None:
        issue = {
            "id": "L-6",
            "title": "T",
            "description": "",
            "state": {"name": "in review"},
        }
        bt = _linear_issue_to_board(issue)
        assert bt.state == BoardTaskState.IN_REVIEW


class TestLinearAdapter:
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch(
            "relay_teams.boards.linear_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client(
                {"data": {"team": {"issues": {"nodes": []}}}}
            )
            result = await adapter.list_tasks(board_id="team-1")
            assert result == ()

    @pytest.mark.asyncio
    async def test_list_tasks_error(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch(
            "relay_teams.boards.linear_adapter.create_async_http_client"
        ) as factory:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.TransportError("network error")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            factory.return_value = mock_client
            result = await adapter.list_tasks(board_id="team-1")
            assert result == ()

    @pytest.mark.asyncio
    async def test_add_artifact_delegates(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch.object(
            adapter, "add_comment", new_callable=AsyncMock
        ) as mock_comment:
            await adapter.add_artifact(task_id="L-1", name="CI", url="https://ci/1")
            mock_comment.assert_called_once()
            assert "CI" in mock_comment.call_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_list_tasks_with_items(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch(
            "relay_teams.boards.linear_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client(
                {
                    "data": {
                        "team": {
                            "issues": {
                                "nodes": [
                                    {
                                        "id": "L-1",
                                        "title": "Test",
                                        "description": "desc",
                                        "state": {"name": "todo"},
                                    }
                                ]
                            }
                        }
                    }
                }
            )
            result = await adapter.list_tasks(board_id="team-1")
            assert len(result) == 1
            assert result[0].board_task_id == "L-1"

    @pytest.mark.asyncio
    async def test_move_task(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch(
            "relay_teams.boards.linear_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client({"data": {"issueUpdate": {}}})
            await adapter.move_task(task_id="L-1", to_state=BoardTaskState.COMPLETED)

    @pytest.mark.asyncio
    async def test_assign_task(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch(
            "relay_teams.boards.linear_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client(
                {"data": {"issueUpdate": {"issue": {}}}}
            )
            await adapter.assign_task(task_id="L-1", assignee="alice")

    @pytest.mark.asyncio
    async def test_add_comment(self) -> None:
        from relay_teams.boards.linear_adapter import (
            LinearAdapter,
        )

        adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
        with patch(
            "relay_teams.boards.linear_adapter.create_async_http_client"
        ) as factory:
            factory.return_value = _mock_async_client(
                {"data": {"commentCreate": {"success": True}}}
            )
            await adapter.add_comment(task_id="L-1", body="LGTM")
