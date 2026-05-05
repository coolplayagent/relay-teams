# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pydantic import JsonValue

from relay_teams.agents.orchestration.board.adapter import (
    BoardTaskState,
)
from relay_teams.agents.orchestration.board.internal_adapter import (
    InternalBoardAdapter,
    _board_to_task_status,
    _record_to_board_task,
    _task_status_to_board,
)
from relay_teams.agents.orchestration.board.linear_adapter import (
    _linear_issue_to_board,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan


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
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = []
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            result = await adapter.list_tasks(board_id="org/repo")
            assert result == ()

    @pytest.mark.asyncio
    async def test_move_task(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            await adapter.move_task(task_id="1", to_state=BoardTaskState.COMPLETED)

    @pytest.mark.asyncio
    async def test_add_comment(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            await adapter.add_comment(task_id="1", body="LGTM")

    @pytest.mark.asyncio
    async def test_add_artifact_uses_comment(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_factory.return_value = MagicMock()
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            with patch.object(
                adapter, "add_comment", new_callable=AsyncMock
            ) as mock_comment:
                await adapter.add_artifact(task_id="1", name="PR", url="https://pr/1")
                mock_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_tasks_with_issues(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = [
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
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            result = await adapter.list_tasks(board_id="org/repo")
            assert len(result) == 1
            assert result[0].board_task_id == "42"
            assert result[0].assignee == "dev"
            assert "bug" in result[0].labels

    @pytest.mark.asyncio
    async def test_list_tasks_error(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )
        import httpx

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_client.request.side_effect = httpx.ConnectError("network error")
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            result = await adapter.list_tasks(board_id="org/repo")
            assert result == ()

    @pytest.mark.asyncio
    async def test_list_tasks_non_list_response(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"message": "error"}
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            result = await adapter.list_tasks(board_id="org/repo")
            assert result == ()

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "number": 1,
                "title": "Test issue",
                "body": "body",
                "state": "open",
            }
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            bt = await adapter.get_task(task_id="1")
            assert bt.board_task_id == "1"

    @pytest.mark.asyncio
    async def test_assign_task(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
            GitHubAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.github_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            adapter = GitHubAdapter(github_repo="org/repo", github_token="fake_token")
            await adapter.assign_task(task_id="1", assignee="dev")

    def test_github_issue_to_board_full(self) -> None:
        from relay_teams.agents.orchestration.board.github_adapter import (
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
        from relay_teams.agents.orchestration.board.github_adapter import (
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
        from relay_teams.agents.orchestration.board.github_adapter import (
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
        from relay_teams.agents.orchestration.board.github_adapter import (
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
        from relay_teams.agents.orchestration.board.github_adapter import (
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
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": {"team": {"issues": {"nodes": []}}}
            }
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            result = await adapter.list_tasks(board_id="team-1")
            assert result == ()

    @pytest.mark.asyncio
    async def test_list_tasks_error(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )
        import httpx

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_client.request.side_effect = httpx.ConnectError("network error")
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            result = await adapter.list_tasks(board_id="team-1")
            assert result == ()

    @pytest.mark.asyncio
    async def test_add_artifact_delegates(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_factory.return_value = MagicMock()
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            with patch.object(
                adapter, "add_comment", new_callable=AsyncMock
            ) as mock_comment:
                await adapter.add_artifact(task_id="L-1", name="CI", url="https://ci/1")
                mock_comment.assert_called_once()
                assert "CI" in mock_comment.call_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_list_tasks_with_items(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {
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
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            result = await adapter.list_tasks(board_id="team-1")
            assert len(result) == 1
            assert result[0].board_task_id == "L-1"

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": {
                    "issue": {
                        "id": "L-42",
                        "title": "Found",
                        "description": "desc",
                        "state": {"name": "started"},
                    }
                }
            }
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            bt = await adapter.get_task(task_id="L-42")
            assert bt.board_task_id == "L-42"
            assert bt.state == BoardTaskState.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_move_task(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": {"issueUpdate": {}}}
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            await adapter.move_task(task_id="L-1", to_state=BoardTaskState.COMPLETED)

    @pytest.mark.asyncio
    async def test_assign_task(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": {"issueUpdate": {"issue": {}}}}
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            await adapter.assign_task(task_id="L-1", assignee="alice")

    @pytest.mark.asyncio
    async def test_add_comment(self) -> None:
        from relay_teams.agents.orchestration.board.linear_adapter import (
            LinearAdapter,
        )

        with patch(
            "relay_teams.agents.orchestration.board.linear_adapter.create_runtime_sync_http_client"
        ) as mock_factory:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": {"commentCreate": {"success": True}}
            }
            mock_client.request.return_value = mock_response
            mock_factory.return_value = mock_client
            adapter = LinearAdapter(api_key="fake_key", team_id="team-1")
            await adapter.add_comment(task_id="L-1", body="LGTM")
