from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

import pytest
from pydantic import JsonValue, ValidationError

from relay_teams.boards import (
    BoardTodoRepository,
    BoardTodoService,
    BoardTodoStatus,
    BoardTodoStatusUpdateRequest,
    BoardTodoSyncChangesRequest,
)
from relay_teams.boards.todo_models import (
    BoardTodoArchiveRequest,
    BoardTodoAttempt,
    BoardTodoAttemptStatus,
    BoardTodoAttemptType,
    BoardTodoExecutionPolicy,
    BoardTodoExecutionQueueTicket,
    BoardTodoHandoffPrompt,
    BoardTodoHandoffTemplate,
    BoardTodoHandoffTemplateInput,
    BoardTodoHandoffTemplateKind,
    BoardTodoItem,
    BoardTodoLinkPullRequestRequest,
    BoardTodoMarkDoneRequest,
    BoardTodoPreviewRequestChangesRequest,
    BoardTodoPreviewStartRequest,
    BoardTodoConcurrencySnapshot,
    BoardTodoQueueKind,
    BoardTodoQueueStatus,
    BoardTodoRuntimeTargetKind,
    BoardTodoRuntimeTargetOption,
    BoardTodoScope,
    BoardTodoSource,
    BoardTodoSourceCreateRequest,
    BoardTodoSourceKind,
    BoardTodoSourceProvider,
    BoardTodoSourceType,
    BoardTodoSourceUpdateRequest,
    BoardTodoSyncStatus,
    BoardTodoStartRequest,
    BoardTodoTemplateScope,
)
from relay_teams.media import content_parts_to_text
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import SessionMode, SessionRecord
from relay_teams.triggers.github_client import GitHubApiError
from relay_teams.triggers.models import (
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
)
from relay_teams.workspace.workspace_models import (
    WorkspaceRecord,
    build_local_workspace_mount,
)
from relay_teams.boards.todo_service import (
    GitHubApiClientLike,
    GitHubTriggerServiceLike,
    RunRuntimeRepositoryLike,
    SessionRunServiceLike,
    SessionServiceLike,
    WorkspaceServiceLike,
    _first_github_remote_url,
    _format_github_sync_error,
    _default_execution_policy,
    _execution_workspace_preview,
    _json_datetime_or_none,
    _json_int,
    _linked_pull_request_from_events,
    _parse_github_pull_request_url,
    _parse_github_remote,
    _queue_ticket_can_be_claimed,
    _queue_ticket_sorts_after,
    _resolve_runtime_target,
    _role_id_from_runtime_target,
    _start_session_topology,
    _target_role_id_for_run,
    _validate_runtime_target_matches_session,
)
from relay_teams.boards.todo_repository import (
    _diagnostics_from_json,
    _execution_policy_or_none,
    _queue_ticket_is_pending_or_expired,
    _row_to_attempt_or_none,
    _row_to_handoff_prompt_or_none,
    _row_to_handoff_template_or_none,
    _row_to_queue_ticket_or_none,
    _runtime_target_kind_or_none,
    _session_mode_or_none,
    _thinking_from_json,
)


class _BoardTodoServiceHarness(BoardTodoService):
    @property
    def repository_for_tests(self) -> BoardTodoRepository:
        return self._repository


def test_board_todo_source_validation_rejects_mismatched_provider() -> None:
    with pytest.raises(ValidationError, match="manual board todo sources"):
        BoardTodoSource(
            source_id="bsrc_manual",
            workspace_id="repo",
            kind=BoardTodoSourceKind.MANUAL,
            provider=BoardTodoSourceProvider.GITHUB,
            display_name="Manual",
        )
    with pytest.raises(ValidationError, match="github_issues sources"):
        BoardTodoSource(
            source_id="bsrc_github",
            workspace_id="repo",
            kind=BoardTodoSourceKind.GITHUB_ISSUES,
            provider=BoardTodoSourceProvider.LOCAL,
            display_name="GitHub",
            repository_full_name="owner/repo",
        )
    with pytest.raises(ValidationError, match="require repository_full_name"):
        BoardTodoSource(
            source_id="bsrc_missing_repo",
            workspace_id="repo",
            kind=BoardTodoSourceKind.GITHUB_ISSUES,
            provider=BoardTodoSourceProvider.GITHUB,
            display_name="GitHub",
        )


def test_board_todo_handoff_template_validation_rejects_bad_scope() -> None:
    with pytest.raises(ValidationError, match="require source_id"):
        BoardTodoHandoffTemplate(
            template_id="btemplate_source_missing",
            workspace_id="repo",
            scope=BoardTodoTemplateScope.SOURCE,
            template_kind=BoardTodoHandoffTemplateKind.START,
            template="Start",
        )
    with pytest.raises(ValidationError, match="cannot include source_id"):
        BoardTodoHandoffTemplate(
            template_id="btemplate_workspace_with_source",
            workspace_id="repo",
            scope=BoardTodoTemplateScope.WORKSPACE,
            source_id="bsrc_github",
            template_kind=BoardTodoHandoffTemplateKind.START,
            template="Start",
        )


class _GetWorkspaceStub(Protocol):
    def __call__(
        self, self_obj: object, workspace_id: str
    ) -> Awaitable[WorkspaceRecord]:
        raise NotImplementedError


class _ForkWorkspaceStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        source_workspace_id: str,
        *,
        name: str,
        start_ref: str | None = None,
    ) -> Awaitable[WorkspaceRecord]:
        raise NotImplementedError


class _DeleteWorkspaceStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        *,
        workspace_id: str,
        remove_directory: bool = False,
    ) -> Awaitable[WorkspaceRecord]:
        raise NotImplementedError


class _ListAccountsStub(Protocol):
    def __call__(
        self,
        self_obj: object,
    ) -> Awaitable[tuple[GitHubTriggerAccountRecord, ...]]:
        raise NotImplementedError


class _ResolveAccountTokenStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        account_id: str,
    ) -> Awaitable[str | None]:
        raise NotImplementedError


class _ListRepositoryIssuesStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        *,
        token: str,
        owner: str,
        repo: str,
        state: str = "all",
        updated_since: datetime | None = None,
    ) -> Awaitable[tuple[dict[str, JsonValue], ...]]:
        raise NotImplementedError


class _ListRepositoryPullRequestsStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        *,
        token: str,
        owner: str,
        repo: str,
        state: str = "all",
        updated_since: datetime | None = None,
    ) -> Awaitable[tuple[dict[str, JsonValue], ...]]:
        raise NotImplementedError


class _GetRepositoryPullRequestStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        *,
        token: str,
        owner: str,
        repo: str,
        pull_request_number: int,
    ) -> Awaitable[dict[str, JsonValue]]:
        raise NotImplementedError


class _ListIssueTimelineEventsStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> Awaitable[tuple[dict[str, JsonValue], ...]]:
        raise NotImplementedError


class _CreateSessionStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> Awaitable[SessionRecord]:
        raise NotImplementedError


class _GetSessionStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        session_id: str,
    ) -> Awaitable[SessionRecord]:
        raise NotImplementedError


class _DeleteSessionStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> Awaitable[None]:
        raise NotImplementedError


class _CreateRunStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> Awaitable[tuple[str, str]]:
        raise NotImplementedError


class _EnsureRunStartedStub(Protocol):
    def __call__(self, self_obj: object, run_id: str) -> Awaitable[None]:
        raise NotImplementedError


class _StopRunStub(Protocol):
    def __call__(self, self_obj: object, run_id: str) -> Awaitable[None]:
        raise NotImplementedError


class _GetRuntimeStub(Protocol):
    def __call__(
        self,
        self_obj: object,
        run_id: str,
    ) -> Awaitable[RunRuntimeRecord | None]:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_sync_creates_independent_boards_per_workspace(tmp_path: Path) -> None:
    workspace_one = _workspace(tmp_path / "one", "owner/one")
    workspace_two = _workspace(tmp_path / "two", "owner/two")
    service = _service(
        tmp_path,
        workspaces=(workspace_one, workspace_two),
        github=_GitHubClient(
            {
                "owner/one": (("Issue one", 1),),
                "owner/two": (("Issue two", 2),),
            }
        ),
    )

    board_one = await service.sync_board(workspace_id="one")
    board_two = await service.sync_board(workspace_id="two")

    assert [item.title for item in board_one.items] == ["Issue one"]
    assert [item.title for item in board_two.items] == ["Issue two"]
    assert board_one.items[0].workspace_id == "one"
    assert board_two.items[0].workspace_id == "two"
    assert board_one.items[0].source_updated_at == datetime(
        2026, 5, 10, 8, 7, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_manual_local_items_are_filtered_from_board_load(tmp_path: Path) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo_manual",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Invalid local item",
            source_provider=BoardTodoSourceProvider.LOCAL,
            source_type=BoardTodoSourceType.MANUAL,
            source_key="manual:todo_manual",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    service = _service(tmp_path, workspaces=(workspace,), repository=repository)

    board = await service.list_board(workspace_id="repo")

    assert board.items == ()
    assert board.source_groups == ()


@pytest.mark.asyncio
async def test_sources_auto_initialize_from_workspace_remote(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))

    settings = await service.list_sources(workspace_id="repo")
    github_sources = [
        view.source
        for view in settings.sources
        if view.source.kind == BoardTodoSourceKind.GITHUB_ISSUES
    ]

    assert settings.board_workspace_id == "repo"
    assert all(
        view.source.kind != BoardTodoSourceKind.MANUAL for view in settings.sources
    )
    assert [source.repository_full_name for source in github_sources] == ["owner/repo"]
    assert [source.enabled for source in github_sources] == [True]


@pytest.mark.asyncio
async def test_source_groups_do_not_include_manual_without_external_source(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))

    board = await service.list_board(workspace_id="repo")

    assert board.source_groups == ()
    assert board.items == ()


@pytest.mark.asyncio
async def test_sync_uses_user_configured_source_repository(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/remote")
    github = _GitHubClient({"owner/configured": (("Configured issue", 44),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    settings = await service.list_sources(workspace_id="repo")
    github_source = next(
        view.source
        for view in settings.sources
        if view.source.kind == BoardTodoSourceKind.GITHUB_ISSUES
    )
    await service.update_source(
        source_id=github_source.source_id,
        payload=BoardTodoSourceUpdateRequest(
            workspace_id="repo",
            repository_full_name="owner/configured",
            display_name="Configured",
        ),
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.repository_full_name == "owner/configured"
    assert [item.title for item in board.items] == ["Configured issue"]


@pytest.mark.asyncio
async def test_github_source_repository_identity_is_case_insensitive(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))

    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Mixed Case",
            repository_full_name="Owner/Repo",
        )
    )

    assert source.repository_full_name == "owner/repo"
    with pytest.raises(ValueError, match="already exists"):
        await service.create_source(
            BoardTodoSourceCreateRequest(
                workspace_id="repo",
                display_name="Lowercase",
                repository_full_name="owner/repo",
            )
        )


@pytest.mark.asyncio
async def test_concurrent_github_source_create_keeps_repository_unique(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))

    results = await asyncio.gather(
        service.create_source(
            BoardTodoSourceCreateRequest(
                workspace_id="repo",
                display_name="First",
                repository_full_name="Owner/Repo",
            )
        ),
        service.create_source(
            BoardTodoSourceCreateRequest(
                workspace_id="repo",
                display_name="Second",
                repository_full_name="owner/repo",
            )
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, BoardTodoSource) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    settings = await service.list_sources(workspace_id="repo")
    assert [view.source.repository_full_name for view in settings.sources] == [
        "owner/repo"
    ]


@pytest.mark.asyncio
async def test_concurrent_github_source_updates_keep_repository_unique(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))
    first = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="First",
            repository_full_name="owner/first",
        )
    )
    second = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Second",
            repository_full_name="owner/second",
        )
    )

    results = await asyncio.gather(
        service.update_source(
            source_id=first.source_id,
            payload=BoardTodoSourceUpdateRequest(
                workspace_id="repo",
                repository_full_name="Owner/Shared",
            ),
        ),
        service.update_source(
            source_id=second.source_id,
            payload=BoardTodoSourceUpdateRequest(
                workspace_id="repo",
                repository_full_name="owner/shared",
            ),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, BoardTodoSource) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    settings = await service.list_sources(workspace_id="repo")
    assert (
        sum(
            view.source.repository_full_name == "owner/shared"
            for view in settings.sources
        )
        == 1
    )


@pytest.mark.asyncio
async def test_default_github_source_bootstrap_is_atomic(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "Owner/Repo")
    service = _service(tmp_path, workspaces=(workspace,))

    await asyncio.gather(
        service.list_sources(workspace_id="repo"),
        service.list_sources(workspace_id="repo"),
    )

    settings = await service.list_sources(workspace_id="repo")
    github_sources = [
        view.source
        for view in settings.sources
        if view.source.kind == BoardTodoSourceKind.GITHUB_ISSUES
    ]
    assert [source.repository_full_name for source in github_sources] == ["owner/repo"]


@pytest.mark.asyncio
async def test_github_source_repository_change_resets_sync_state(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))
    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )
    previous_sync_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    await service.repository_for_tests.update_source_sync_state_async(
        source_id=source.source_id,
        workspace_id="repo",
        sync_cursor=previous_sync_time,
        status=BoardTodoSyncStatus.SUCCEEDED,
        diagnostics=("old repository cursor",),
        started_at=previous_sync_time,
        finished_at=previous_sync_time,
    )

    await service.update_source(
        source_id=source.source_id,
        payload=BoardTodoSourceUpdateRequest(
            workspace_id="repo",
            repository_full_name="owner/other",
        ),
    )

    state = await service.repository_for_tests.get_source_state_async(
        source_id=source.source_id
    )
    assert state is not None
    assert state.sync_cursor is None
    assert state.last_sync_status == BoardTodoSyncStatus.IDLE
    assert state.last_diagnostics == ()
    assert state.last_sync_started_at is None
    assert state.last_sync_finished_at is None


@pytest.mark.asyncio
async def test_github_source_crud_rejects_delete_after_import(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    github = _GitHubClient({"owner/repo": (("Imported issue", 45),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )
    await service.update_source(
        source_id=source.source_id,
        payload=BoardTodoSourceUpdateRequest(
            workspace_id="repo",
            display_name="Updated Repo",
            enabled=True,
        ),
    )

    board = await service.sync_board(workspace_id="repo")

    assert [item.source_id for item in board.items] == [source.source_id]
    with pytest.raises(ValueError, match="disable it instead"):
        await service.delete_source(source_id=source.source_id)


@pytest.mark.asyncio
async def test_github_source_crud_rejects_blank_display_name(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))
    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )

    with pytest.raises(ValueError, match="display_name cannot be blank"):
        await service.update_source(
            source_id=source.source_id,
            payload=BoardTodoSourceUpdateRequest(
                workspace_id="repo",
                display_name="   ",
            ),
        )


@pytest.mark.asyncio
async def test_github_source_repository_change_rejected_after_import(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    github = _GitHubClient({"owner/repo": (("Imported issue", 45),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )
    await service.sync_board(workspace_id="repo")

    with pytest.raises(ValueError, match="cannot be changed after importing"):
        await service.update_source(
            source_id=source.source_id,
            payload=BoardTodoSourceUpdateRequest(
                workspace_id="repo",
                repository_full_name="owner/other",
            ),
        )


@pytest.mark.asyncio
async def test_delete_source_counts_legacy_imported_items(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))
    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )
    await _create_test_todo(
        service,
        workspace_id="repo",
        title="Legacy imported issue",
        repository_full_name="Owner/Repo",
    )

    with pytest.raises(ValueError, match="disable it instead"):
        await service.delete_source(source_id=source.source_id)


@pytest.mark.asyncio
async def test_unused_github_source_can_be_deleted(tmp_path: Path) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))
    source = await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )

    deleted = await service.delete_source(source_id=source.source_id)
    settings = await service.list_sources(workspace_id="repo")

    assert deleted.deleted is True
    assert source.source_id not in {view.source.source_id for view in settings.sources}


@pytest.mark.asyncio
async def test_deleted_auto_initialized_source_is_not_recreated(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    settings = await service.list_sources(workspace_id="repo")
    source = settings.sources[0].source

    await service.delete_source(source_id=source.source_id)
    next_settings = await service.list_sources(workspace_id="repo")

    assert next_settings.sources == ()


@pytest.mark.asyncio
async def test_duplicate_github_source_repository_is_rejected(
    tmp_path: Path,
) -> None:
    workspace = _workspace_without_remote(tmp_path / "repo")
    service = _service(tmp_path, workspaces=(workspace,))
    await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Owner Repo",
            repository_full_name="owner/repo",
        )
    )

    with pytest.raises(ValueError, match="already exists"):
        await service.create_source(
            BoardTodoSourceCreateRequest(
                workspace_id="repo",
                display_name="Duplicate",
                repository_full_name="owner/repo",
            )
        )


@pytest.mark.asyncio
async def test_multiple_github_sources_keep_independent_cursors(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/one")
    github = _GitHubClient(
        {
            "owner/one": (("One", 1),),
            "owner/two": (("Two", 2),),
        }
    )
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    await service.list_sources(workspace_id="repo")
    await service.create_source(
        BoardTodoSourceCreateRequest(
            workspace_id="repo",
            display_name="Two",
            repository_full_name="owner/two",
        )
    )

    board = await service.sync_board(workspace_id="repo")
    settings = await service.list_sources(workspace_id="repo")
    github_views = [
        view
        for view in settings.sources
        if view.source.kind == BoardTodoSourceKind.GITHUB_ISSUES
    ]

    assert sorted(item.title for item in board.items) == ["One", "Two"]
    assert len(github_views) == 2
    assert all(view.state is not None for view in github_views)
    assert {view.source.source_id for view in github_views} == {
        view.state.source_id for view in github_views if view.state is not None
    }
    assert all(
        view.state.sync_cursor is not None for view in github_views if view.state
    )


@pytest.mark.asyncio
async def test_disabled_source_is_not_synced(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient({"owner/repo": (("Issue", 45),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    settings = await service.list_sources(workspace_id="repo")
    github_source = next(
        view.source
        for view in settings.sources
        if view.source.kind == BoardTodoSourceKind.GITHUB_ISSUES
    )
    await service.update_source(
        source_id=github_source.source_id,
        payload=BoardTodoSourceUpdateRequest(workspace_id="repo", enabled=False),
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.items == ()
    assert github.tokens == []
    assert board.diagnostics == (
        "No enabled GitHub TODO source is configured for this board.",
    )


@pytest.mark.asyncio
async def test_preview_start_returns_prompt_and_start_requires_final_prompt(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="Body",
    )

    preview = await service.preview_start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoPreviewStartRequest(view_workspace_id="repo"),
    )

    assert "Title: Imported issue" in preview.prompt
    assert preview.session_mode is None
    assert preview.yolo is True
    assert preview.thinking == RunThinkingConfig()
    assert run_service.count == 0
    with pytest.raises(ValueError, match="final_prompt is required"):
        await service.start_todo(
            todo_id=item.todo_id,
            payload=BoardTodoStartRequest(),
        )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Edited prompt"),
    )
    assert started.status == BoardTodoStatus.IN_PROGRESS
    assert run_service.prompts == ["Edited prompt"]
    attempts = await service.repository_for_tests.list_attempts_for_todo_async(
        item.todo_id
    )
    assert len(attempts) == 1
    assert attempts[0].attempt_type == BoardTodoAttemptType.START
    assert attempts[0].status == BoardTodoAttemptStatus.ACTIVE
    assert started.current_attempt_id == attempts[0].attempt_id
    assert started.active_attempt_id == attempts[0].attempt_id
    assert attempts[0].prompt_ref is not None
    prompt = await service.repository_for_tests.require_handoff_prompt_async(
        attempts[0].prompt_ref
    )
    assert prompt.template_kind == "start"
    assert prompt.final_prompt_snapshot == "Edited prompt"


@pytest.mark.asyncio
async def test_start_defaults_to_fork_execution_workspace(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=_RunService(run_runtime),
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Fork this issue",
    )

    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Edited prompt"),
    )

    assert started.execution_policy == BoardTodoExecutionPolicy.FORK_GIT_WORKTREE
    assert started.execution_workspace_id is not None
    assert started.execution_workspace_id != "repo"
    assert session_service.workspace_ids == [started.execution_workspace_id]


@pytest.mark.asyncio
async def test_start_derives_session_topology_from_runtime_target(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Preset target",
    )

    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Edited prompt",
            runtime_target_id="preset:review",
        ),
    )

    assert started.runtime_target_id == "preset:review"
    assert session_service.session_modes == [SessionMode.ORCHESTRATION]
    assert session_service.orchestration_preset_ids == ["review"]
    assert run_service.intents[0].session_mode == SessionMode.ORCHESTRATION


@pytest.mark.asyncio
async def test_start_queues_when_runtime_target_slot_is_full_and_drains(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )

    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )

    assert queued.status == BoardTodoStatus.IN_PROGRESS
    assert queued.queue_ticket_id is not None
    assert run_service.count == 0
    ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )
    assert ticket.status == BoardTodoQueueStatus.PENDING

    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
                "queue_ticket_id": None,
            }
        )
    )
    drained = await service.drain_queue_once()
    updated = await service.repository_for_tests.require_async(item.todo_id)
    completed_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert run_service.count == 1
    assert updated.run_id == "run-1"
    assert updated.queue_ticket_id is None
    assert completed_ticket.status == BoardTodoQueueStatus.COMPLETED


@pytest.mark.asyncio
async def test_pending_queue_ticket_reserves_runtime_target_capacity(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    first = await _create_test_todo(
        service,
        workspace_id="repo",
        title="First queued issue",
    )
    second = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Second queued issue",
    )
    first_queued = await service.start_todo(
        todo_id=first.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert first_queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    second_queued = await service.start_todo(
        todo_id=second.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Second prompt"),
    )

    assert second_queued.queue_ticket_id is not None
    assert second_queued.run_id is None
    assert run_service.count == 0


@pytest.mark.asyncio
async def test_legacy_active_run_without_runtime_target_reserves_session_target(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active_session = await session_service.create_session_async(
        workspace_id="repo",
        session_mode=SessionMode.NORMAL,
        normal_root_role_id="main_agent",
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Legacy active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "session_id": active_session.session_id,
                "run_id": "run-active",
                "active_attempt_id": "attempt-active",
                "runtime_target_id": None,
                "runtime_target_kind": None,
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id=active_session.session_id,
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued behind legacy issue",
    )

    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process queued"),
    )

    assert queued.status == BoardTodoStatus.IN_PROGRESS
    assert queued.queue_ticket_id is not None
    assert queued.run_id is None
    assert run_service.count == 0


@pytest.mark.asyncio
async def test_drain_queue_lets_oldest_pending_ticket_consume_free_slot(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    first = await _create_test_todo(
        service,
        workspace_id="repo",
        title="First queued issue",
    )
    second = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Second queued issue",
    )
    first_queued = await service.start_todo(
        todo_id=first.todo_id,
        payload=BoardTodoStartRequest(final_prompt="First prompt"),
    )
    second_queued = await service.start_todo(
        todo_id=second.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Second prompt"),
    )
    assert first_queued.queue_ticket_id is not None
    assert second_queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()
    first_after = await service.repository_for_tests.require_async(first.todo_id)
    second_after = await service.repository_for_tests.require_async(second.todo_id)

    assert drained == 1
    assert run_service.count == 1
    assert first_after.run_id == "run-1"
    assert first_after.queue_ticket_id is None
    assert second_after.run_id is None
    assert second_after.queue_ticket_id == second_queued.queue_ticket_id


@pytest.mark.asyncio
async def test_drain_queue_releases_claim_when_capacity_fills_after_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process queued"),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    async def fake_concurrency_snapshot(
        *,
        source_workspace_id: str,
        runtime_target_id: str,
        excluded_queue_ticket_id: str | None = None,
        excluded_todo_id: str | None = None,
        include_claimable_queue_tickets: bool = True,
        capacity_before_ticket: BoardTodoExecutionQueueTicket | None = None,
    ) -> BoardTodoConcurrencySnapshot:
        del (
            source_workspace_id,
            runtime_target_id,
            excluded_queue_ticket_id,
            excluded_todo_id,
            include_claimable_queue_tickets,
        )
        runtime_target_active = 1 if capacity_before_ticket is not None else 0
        return BoardTodoConcurrencySnapshot(
            source_workspace_active=runtime_target_active,
            source_workspace_limit=2,
            runtime_target_active=runtime_target_active,
            runtime_target_limit=1,
        )

    monkeypatch.setattr(service, "_concurrency_snapshot", fake_concurrency_snapshot)

    drained = await service.drain_queue_once()

    released_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )
    assert drained == 0
    assert released_ticket.status == BoardTodoQueueStatus.PENDING
    assert released_ticket.claim_token is None
    assert released_ticket.claim_expires_at is None
    assert run_service.count == 0


@pytest.mark.asyncio
async def test_drain_queue_skips_blocked_prefix_for_later_free_target(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    first_main = await _create_test_todo(
        service,
        workspace_id="repo",
        title="First main target",
    )
    second_main = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Second main target",
    )
    reviewer = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Reviewer target",
    )
    first_main_queued = await service.start_todo(
        todo_id=first_main.todo_id,
        payload=BoardTodoStartRequest(final_prompt="First main prompt"),
    )
    second_main_queued = await service.start_todo(
        todo_id=second_main.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Second main prompt"),
    )
    reviewer_queued = await service.start_todo(
        todo_id=reviewer.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Reviewer prompt",
            runtime_target_id="role:reviewer",
        ),
    )
    assert first_main_queued.queue_ticket_id is not None
    assert second_main_queued.queue_ticket_id is not None
    assert reviewer_queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()
    first_main_after = await service.repository_for_tests.require_async(
        first_main.todo_id
    )
    second_main_after = await service.repository_for_tests.require_async(
        second_main.todo_id
    )
    reviewer_after = await service.repository_for_tests.require_async(reviewer.todo_id)

    assert drained == 2
    assert run_service.count == 2
    assert first_main_after.run_id == "run-1"
    assert second_main_after.run_id is None
    assert second_main_after.queue_ticket_id == second_main_queued.queue_ticket_id
    assert reviewer_after.run_id == "run-2"
    assert reviewer_after.queue_ticket_id is None


@pytest.mark.asyncio
async def test_drain_queue_reclaims_expired_claimed_ticket(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )
    await service.repository_for_tests.update_queue_ticket_async(
        ticket.model_copy(
            update={
                "status": BoardTodoQueueStatus.CLAIMED,
                "claim_expires_at": _now() - timedelta(minutes=1),
                "claimed_by": "previous-worker",
            }
        )
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()
    updated = await service.repository_for_tests.require_async(item.todo_id)
    completed_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert run_service.count == 1
    assert updated.run_id == "run-1"
    assert updated.queue_ticket_id is None
    assert completed_ticket.status == BoardTodoQueueStatus.COMPLETED
    assert completed_ticket.claimed_by == "board-todo-queue-worker"


@pytest.mark.asyncio
async def test_queue_ticket_claim_is_atomic_for_live_claim(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(service, workspace_id="repo", title="Queued")
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )
    now = _now()

    first_claim = await service.repository_for_tests.claim_queue_ticket_async(
        ticket=ticket,
        claim_token="first",
        claim_expires_at=now + timedelta(minutes=1),
        claimed_by="first-worker",
        now=now,
    )
    second_claim = await service.repository_for_tests.claim_queue_ticket_async(
        ticket=ticket,
        claim_token="second",
        claim_expires_at=now + timedelta(minutes=1),
        claimed_by="second-worker",
        now=now,
    )

    assert first_claim is not None
    assert second_claim is None
    stored = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )
    assert stored.claim_token == "first"


@pytest.mark.asyncio
async def test_drain_queue_cancels_ticket_that_no_longer_owns_item(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    await service.archive_todo(
        todo_id=queued.todo_id,
        payload=BoardTodoArchiveRequest(reason="No longer needed"),
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()
    archived = await service.repository_for_tests.require_async(item.todo_id)
    cancelled_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert run_service.count == 0
    assert archived.status == BoardTodoStatus.ARCHIVED
    assert cancelled_ticket.status == BoardTodoQueueStatus.CANCELLED
    assert cancelled_ticket.diagnostics == (
        "Queued handoff ticket no longer owns TODO item",
    )


@pytest.mark.asyncio
async def test_queued_start_stale_after_session_creation_cleans_prepared_resources(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    workspace_service = _WorkspaceService((workspace,))
    session_service = _BlockingSessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        workspace_service=workspace_service,
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drain_task = asyncio.create_task(service.drain_queue_once())
    await session_service.entered.wait()
    await service.archive_todo(
        todo_id=queued.todo_id,
        payload=BoardTodoArchiveRequest(reason="No longer needed"),
    )
    session_service.release.set()
    drained = await drain_task
    archived = await service.repository_for_tests.require_async(item.todo_id)
    cancelled_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert run_service.count == 0
    assert archived.status == BoardTodoStatus.ARCHIVED
    assert cancelled_ticket.status == BoardTodoQueueStatus.CANCELLED
    assert cancelled_ticket.diagnostics == (
        "Queued handoff ticket no longer owns TODO item before run start",
    )
    assert session_service.deleted_session_ids == ["session-1"]
    assert "session-1" not in session_service.sessions
    assert len(workspace_service.deleted_workspace_ids) == 1
    assert workspace_service.deleted_workspace_ids[0] != "repo"


@pytest.mark.asyncio
async def test_queued_start_stale_after_run_creation_cleans_prepared_resources(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    workspace_service = _WorkspaceService((workspace,))
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _BlockingEnsureRunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        workspace_service=workspace_service,
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drain_task = asyncio.create_task(service.drain_queue_once())
    await run_service.entered.wait()
    await service.archive_todo(
        todo_id=queued.todo_id,
        payload=BoardTodoArchiveRequest(reason="No longer needed"),
    )
    run_service.release.set()
    drained = await drain_task
    archived = await service.repository_for_tests.require_async(item.todo_id)
    cancelled_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert archived.status == BoardTodoStatus.ARCHIVED
    assert run_runtime.records["run-1"].status == RunRuntimeStatus.STOPPED
    assert cancelled_ticket.status == BoardTodoQueueStatus.CANCELLED
    assert session_service.deleted_session_ids == ["session-1"]
    assert "session-1" not in session_service.sessions
    assert len(workspace_service.deleted_workspace_ids) == 1
    assert workspace_service.deleted_workspace_ids[0] != "repo"


@pytest.mark.asyncio
async def test_queued_start_cancellation_cleans_prepared_resources(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    workspace_service = _WorkspaceService((workspace,))
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _ControlledRunService(run_runtime, block_on_count=1)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        workspace_service=workspace_service,
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drain_task = asyncio.create_task(service.drain_queue_once())
    await run_service.entered.wait()
    drain_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain_task
    restored = await service.repository_for_tests.require_async(item.todo_id)
    failed_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert restored.status == BoardTodoStatus.TODO
    assert failed_ticket.status == BoardTodoQueueStatus.FAILED
    assert session_service.deleted_session_ids == ["session-1"]
    assert "session-1" not in session_service.sessions
    assert len(workspace_service.deleted_workspace_ids) == 1
    assert workspace_service.deleted_workspace_ids[0] != "repo"
    run_service.release.set()


@pytest.mark.asyncio
async def test_workspace_handoff_template_is_used_for_preview(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Template issue",
    )
    await service.upsert_workspace_handoff_template(
        BoardTodoHandoffTemplateInput(
            workspace_id="repo",
            template_kind=BoardTodoHandoffTemplateKind.START,
            template="Handle {{ todo.title }} in {{ todo.repository }}",
        )
    )

    preview = await service.preview_start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoPreviewStartRequest(view_workspace_id="repo"),
    )

    assert preview.template_source == "workspace:repo"
    assert preview.prompt == "Handle Template issue in owner/repo"


@pytest.mark.asyncio
async def test_start_request_passes_normal_runtime_options(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    session_service = _SessionService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="Body",
    )

    await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Edited prompt",
            session_mode=SessionMode.NORMAL,
            normal_root_role_id="role_dev",
            yolo=False,
            thinking=RunThinkingConfig(enabled=True, effort="high"),
        ),
    )

    assert session_service.session_modes == [SessionMode.NORMAL]
    assert session_service.normal_root_role_ids == ["role_dev"]
    assert session_service.orchestration_preset_ids == [None]
    assert run_service.intents[0].target_role_id == "role_dev"
    assert run_service.intents[0].thinking == RunThinkingConfig(
        enabled=True,
        effort="high",
    )
    assert run_service.intents[0].yolo is False
    assert run_service.intents[0].session_mode == SessionMode.NORMAL


@pytest.mark.asyncio
async def test_start_request_without_mode_uses_matching_runtime_target_default(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    session_service = _SessionService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="Body",
    )

    await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Edited prompt"),
    )

    assert session_service.session_modes == [SessionMode.NORMAL]
    assert session_service.normal_root_role_ids == ["main_agent"]
    assert run_service.intents[0].session_mode == SessionMode.NORMAL


@pytest.mark.asyncio
async def test_start_request_passes_orchestration_runtime_options(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    session_service = _SessionService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="Body",
    )

    await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Edited prompt",
            session_mode=SessionMode.ORCHESTRATION,
            normal_root_role_id="role_ignored",
            orchestration_preset_id="preset_plan",
        ),
    )

    assert session_service.session_modes == [SessionMode.ORCHESTRATION]
    assert session_service.normal_root_role_ids == [None]
    assert session_service.orchestration_preset_ids == ["preset_plan"]
    assert run_service.intents[0].target_role_id is None
    assert run_service.intents[0].session_mode == SessionMode.ORCHESTRATION


@pytest.mark.asyncio
async def test_request_changes_preserves_orchestration_session_mode(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    session_service = _SessionService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Edited prompt",
            session_mode=SessionMode.ORCHESTRATION,
            orchestration_preset_id="preset_plan",
        ),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")

    preview = await service.preview_request_changes_todo(
        todo_id=item.todo_id,
        payload=BoardTodoPreviewRequestChangesRequest(feedback="Please revise"),
    )
    await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Please revise",
            final_prompt=preview.prompt,
        ),
    )

    assert [intent.session_mode for intent in run_service.intents] == [
        SessionMode.ORCHESTRATION,
        SessionMode.ORCHESTRATION,
    ]
    assert run_service.intents[1].target_role_id is None


@pytest.mark.asyncio
async def test_start_from_fork_workspace_uses_view_workspace(
    tmp_path: Path,
) -> None:
    root_workspace = _workspace(tmp_path / "root", "owner/root")
    fork_root = tmp_path / "fork"
    fork_root.mkdir()
    fork_workspace = WorkspaceRecord(
        workspace_id="fork",
        default_mount_name="default",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=fork_root,
                source_root_path=str(root_workspace.root_path or tmp_path / "root"),
                forked_from_workspace_id=root_workspace.workspace_id,
            ),
        ),
        created_at=_now(),
        updated_at=_now(),
    )
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    service = _service(
        tmp_path,
        workspaces=(root_workspace, fork_workspace),
        session_service=session_service,
        run_service=_RunService(run_runtime),
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id=root_workspace.workspace_id,
        title="Fork scoped issue",
    )

    await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            view_workspace_id="fork",
            execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
            final_prompt="Use the fork workspace",
        ),
    )

    assert session_service.workspace_ids == ["fork"]


@pytest.mark.asyncio
async def test_fork_workspace_uses_root_board_sources(tmp_path: Path) -> None:
    root_workspace = _workspace(tmp_path / "root", "owner/root")
    fork_root = tmp_path / "fork"
    fork_root.mkdir()
    fork_workspace = WorkspaceRecord(
        workspace_id="fork",
        default_mount_name="default",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=fork_root,
                source_root_path=str(root_workspace.root_path or tmp_path / "root"),
                forked_from_workspace_id=root_workspace.workspace_id,
            ),
        ),
        created_at=_now(),
        updated_at=_now(),
    )
    service = _service(tmp_path, workspaces=(root_workspace, fork_workspace))

    settings = await service.list_sources(workspace_id="fork")

    assert settings.is_fork_view is True
    assert settings.board_workspace_id == root_workspace.workspace_id


@pytest.mark.asyncio
async def test_sync_uses_non_origin_github_remote(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path / "repo",
        "owner/repo",
        remote_name="coolplayagent",
    )
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient({"owner/repo": (("Issue", 3),)}),
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.repository_full_name == "owner/repo"
    assert board.diagnostics == ()
    assert [item.title for item in board.items] == ["Issue"]


@pytest.mark.asyncio
async def test_sync_reconciles_legacy_mixed_case_issue_source_key(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "Owner/Repo")
    run_runtime = _RunRuntimeRepository()
    github = _GitHubClient()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=github,
        run_runtime=run_runtime,
    )
    legacy = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Legacy issue",
        repository_full_name="Owner/Repo",
    )
    github.set_issues({"owner/repo": (("Updated issue", legacy.issue_number or 0),)})

    board = await service.sync_board(workspace_id="repo")

    assert [item.todo_id for item in board.items] == [legacy.todo_id]
    assert board.items[0].title == "Updated issue"
    assert board.items[0].repository_full_name == "owner/repo"
    assert board.items[0].source_key == (
        f"github:owner/repo:issue:{legacy.issue_number}"
    )


@pytest.mark.asyncio
async def test_full_sync_archives_legacy_mixed_case_closed_issue(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "Owner/Repo")
    github = _GitHubClient()
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    legacy = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Legacy issue",
        repository_full_name="Owner/Repo",
    )
    github.set_issues(
        {"owner/repo": (("Closed issue", legacy.issue_number or 0, "closed"),)}
    )

    board = await service.sync_board(workspace_id="repo")
    stored = await service.repository_for_tests.require_async(legacy.todo_id)

    assert stored.status == BoardTodoStatus.ARCHIVED
    assert stored.last_status_reason == "GitHub issue no longer open"
    assert all(item.todo_id != legacy.todo_id for item in board.items)


@pytest.mark.asyncio
async def test_sync_uses_shared_github_token_without_trigger_account(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient({"owner/repo": (("Issue", 4),)})
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=github,
        github_accounts=(),
        shared_github_token="ghp_shared",
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.diagnostics == ()
    assert github.tokens == ["ghp_shared", "ghp_shared"]
    assert [item.title for item in board.items] == ["Issue"]


@pytest.mark.asyncio
async def test_sync_prefers_trigger_account_token_over_shared_token(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient({"owner/repo": (("Issue", 5),)})
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=github,
        github_tokens={"gh_1": "ghp_account"},
        shared_github_token="ghp_shared",
    )

    await service.sync_board(workspace_id="repo")

    assert github.tokens == ["ghp_account", "ghp_account"]


@pytest.mark.asyncio
async def test_sync_falls_back_to_shared_token_after_account_token_failure(
    tmp_path: Path,
) -> None:
    class _FallbackGitHubClient(_GitHubClient):
        async def list_repository_issues(
            self,
            *,
            token: str,
            owner: str,
            repo: str,
            state: str = "all",
            updated_since: datetime | None = None,
        ) -> tuple[dict[str, JsonValue], ...]:
            if token == "ghp_account":
                self.tokens.append(token)
                raise GitHubApiError(message="Resource not accessible", status_code=403)
            return await super().list_repository_issues(
                token=token,
                owner=owner,
                repo=repo,
                state=state,
                updated_since=updated_since,
            )

    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _FallbackGitHubClient({"owner/repo": (("Issue", 6),)})
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=github,
        github_tokens={"gh_1": "ghp_account"},
        shared_github_token="ghp_shared",
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.diagnostics == (
        "GitHub sync failed for owner/repo (full status=403): Resource not accessible",
    )
    assert github.tokens == ["ghp_account", "ghp_shared", "ghp_shared"]
    assert [item.title for item in board.items] == ["Issue"]


@pytest.mark.asyncio
async def test_sync_reports_missing_github_token_without_account_or_shared_token(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github_accounts=(),
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.diagnostics == (
        "No enabled GitHub trigger account token is available.",
    )


@pytest.mark.asyncio
async def test_sync_reports_non_empty_github_error_diagnostic(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient(
            github_error=GitHubApiError(message="", status_code=403),
        ),
    )

    board = await service.sync_board(workspace_id="repo")

    assert board.diagnostics == (
        "GitHub sync failed for owner/repo (full status=403): "
        "GitHub sync failed with status 403",
    )


@pytest.mark.asyncio
async def test_sync_does_not_import_open_pull_requests_as_todos(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient(
            {"owner/repo": (("Issue", 6),)},
            pull_requests={"owner/repo": (("Open PR", 21, None),)},
        ),
    )

    board = await service.sync_board(workspace_id="repo")

    assert [(item.title, item.source_type) for item in board.items] == [
        ("Issue", BoardTodoSourceType.GITHUB_ISSUE)
    ]


@pytest.mark.asyncio
async def test_sync_does_not_import_closed_issues_as_todos(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient({"owner/repo": (("Closed issue", 7, "closed"),)}),
    )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert board.items == ()


@pytest.mark.asyncio
async def test_sync_archives_existing_closed_issue_without_merged_pr(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": (("Closed issue", 7, "closed"),)}),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-closed",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Closed issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:7",
            repository_full_name="owner/repo",
            issue_number=7,
            created_at=_now(),
            updated_at=_now(),
        )
    )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert board.items[0].status == BoardTodoStatus.ARCHIVED
    assert board.items[0].last_status_reason == "GitHub issue no longer open"


@pytest.mark.asyncio
async def test_incremental_sync_archives_existing_closed_issue_without_merged_pr(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": (("Closed issue", 7, "closed"),)}),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-closed",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Closed issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:7",
            repository_full_name="owner/repo",
            issue_number=7,
            created_at=_now(),
            updated_at=_now(),
        )
    )

    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(workspace_id="repo", after_revision=0)
    )
    board = await service.list_board(workspace_id="repo", include_archived=True)

    assert board.items[0].status == BoardTodoStatus.ARCHIVED
    assert board.items[0].last_status_reason == "GitHub issue closed"


@pytest.mark.asyncio
async def test_sync_marks_existing_closed_issue_done_when_linked_pr_merged(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient(
            {"owner/repo": (("Closed issue", 7, "closed"),)},
            pull_requests={"owner/repo": (("Fix issue", 22, "2026-05-10T01:00:00Z"),)},
        ),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-closed-done",
            workspace_id="repo",
            status=BoardTodoStatus.REVIEW,
            title="Closed issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:7",
            repository_full_name="owner/repo",
            issue_number=7,
            linked_pr_number=22,
            created_at=_now(),
            updated_at=_now(),
        )
    )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert board.items[0].status == BoardTodoStatus.DONE


@pytest.mark.asyncio
async def test_sync_links_closed_review_issue_before_archiving(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient(
            {"owner/repo": (("Closed issue", 7, "closed"),)},
            pull_requests={"owner/repo": (("Fix issue", 22, "2026-05-10T01:00:00Z"),)},
            timelines={"owner/repo#7": (22,)},
        ),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-closed-review",
            workspace_id="repo",
            status=BoardTodoStatus.REVIEW,
            title="Closed issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:7",
            repository_full_name="owner/repo",
            issue_number=7,
            created_at=_now(),
            updated_at=_now(),
        )
    )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert board.items[0].status == BoardTodoStatus.DONE
    assert board.items[0].linked_pr_number == 22


@pytest.mark.asyncio
async def test_full_sync_archives_missing_active_github_issue(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": (("Open issue", 7),)}),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-missing-github-issue",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Missing issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:999",
            repository_full_name="owner/repo",
            issue_number=999,
            created_at=_now(),
            updated_at=_now(),
        )
    )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert {item.issue_number: item.status for item in board.items} == {
        7: BoardTodoStatus.TODO,
        999: BoardTodoStatus.ARCHIVED,
    }
    archived = next(item for item in board.items if item.issue_number == 999)
    assert archived.last_status_reason == "GitHub issue no longer open"


@pytest.mark.asyncio
async def test_full_sync_restores_github_issue_archived_by_closed_sync(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": (("Reopened issue", 7),)}),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-reopened-github-issue",
            workspace_id="repo",
            status=BoardTodoStatus.ARCHIVED,
            title="Closed issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:7",
            repository_full_name="owner/repo",
            issue_number=7,
            session_id="session-old",
            run_id="run-old",
            current_attempt_id="attempt-old",
            active_attempt_id="attempt-old",
            execution_workspace_id="workspace-old",
            execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
            runtime_target_kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
            runtime_target_id="role:reviewer",
            queue_ticket_id="queue-old",
            archived_at=_now(),
            last_status_reason="GitHub issue no longer open",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    board = await service.sync_board(workspace_id="repo")

    assert len(board.items) == 1
    assert board.items[0].status == BoardTodoStatus.TODO
    assert board.items[0].archived_at is None
    assert board.items[0].last_status_reason == "GitHub issue reopened"
    assert board.items[0].session_id is None
    assert board.items[0].run_id is None
    assert board.items[0].current_attempt_id is None
    assert board.items[0].active_attempt_id is None
    assert board.items[0].execution_workspace_id is None
    assert board.items[0].execution_policy is None
    assert board.items[0].runtime_target_kind is None
    assert board.items[0].runtime_target_id is None
    assert board.items[0].queue_ticket_id is None


@pytest.mark.asyncio
async def test_full_sync_preserves_done_github_issue_that_is_still_open(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": (("Reopened issue", 7),)}),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-reopened-done-github-issue",
            workspace_id="repo",
            status=BoardTodoStatus.DONE,
            title="Done issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:7",
            repository_full_name="owner/repo",
            issue_number=7,
            linked_pr_number=17,
            linked_pr_url="https://github.com/owner/repo/pull/17",
            last_status_reason="Pull request merged",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    board = await service.sync_board(workspace_id="repo")

    assert len(board.items) == 1
    assert board.items[0].status == BoardTodoStatus.DONE
    assert board.items[0].linked_pr_number == 17
    assert board.items[0].last_status_reason == "Pull request merged"


@pytest.mark.asyncio
async def test_full_sync_uses_open_issues_as_active_set(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    github = _GitHubClient(
        {
            "owner/repo": (
                ("Open issue", 7),
                ("Closed issue", 8, "closed"),
            )
        }
    )
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=github,
    )
    for number in (7, 8, 9):
        await repository.create_async(
            BoardTodoItem(
                todo_id=f"todo-{number}",
                workspace_id="repo",
                status=BoardTodoStatus.TODO,
                title=f"Issue {number}",
                source_provider=BoardTodoSourceProvider.GITHUB,
                source_type=BoardTodoSourceType.GITHUB_ISSUE,
                source_key=f"github:owner/repo:issue:{number}",
                repository_full_name="owner/repo",
                issue_number=number,
                created_at=_now(),
                updated_at=_now(),
            )
        )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert github.issue_states == ["open"]
    assert {item.issue_number: item.status for item in board.items} == {
        7: BoardTodoStatus.TODO,
        8: BoardTodoStatus.ARCHIVED,
        9: BoardTodoStatus.ARCHIVED,
    }


@pytest.mark.asyncio
async def test_incremental_sync_does_not_archive_missing_active_github_issue(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": (("Open issue", 7),)}),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-missing-github-issue",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Missing issue",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:999",
            repository_full_name="owner/repo",
            issue_number=999,
            created_at=_now(),
            updated_at=_now(),
        )
    )

    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(workspace_id="repo", after_revision=0)
    )
    board = await service.list_board(workspace_id="repo")

    assert {item.issue_number: item.status for item in board.items} == {
        7: BoardTodoStatus.TODO,
        999: BoardTodoStatus.TODO,
    }


@pytest.mark.asyncio
async def test_sync_links_review_issue_to_related_pull_request(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient(
        {"owner/repo": (("Issue", 8),)},
        pull_requests={"owner/repo": (("Fix issue", 22, None),)},
        timelines={"owner/repo#8": (22,)},
    )
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    synced = await service.sync_board(workspace_id="repo")
    started = await service.start_todo(
        todo_id=synced.items[0].todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    await service.mark_run_completed_async(run_id=started.run_id or "")
    board = await service.sync_board(workspace_id="repo")

    assert board.items[0].status == BoardTodoStatus.REVIEW
    assert board.items[0].linked_pr_number == 22
    assert board.items[0].linked_pr_url == "https://github.com/owner/repo/pull/22"


@pytest.mark.asyncio
async def test_sync_marks_issue_done_when_linked_pull_request_is_merged(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient(
        {"owner/repo": (("Issue", 9),)},
        pull_requests={"owner/repo": (("Fix issue", 23, "2026-05-10T09:00:00Z"),)},
        timelines={"owner/repo#9": (23,)},
    )
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    synced = await service.sync_board(workspace_id="repo")
    started = await service.start_todo(
        todo_id=synced.items[0].todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    await service.mark_run_completed_async(run_id=started.run_id or "")
    board = await service.sync_board(workspace_id="repo")

    assert board.items[0].status == BoardTodoStatus.DONE
    assert board.items[0].linked_pr_number == 23


@pytest.mark.asyncio
async def test_sync_prefers_closing_pull_request_over_first_timeline_reference(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient(
        {"owner/repo": (("Issue", 33),)},
        pull_requests={
            "owner/repo": (
                ("Mentioned issue", 40, None),
                ("Fix issue", 41, "2026-05-10T09:00:00Z"),
            )
        },
        timelines={"owner/repo#33": (40, (41, "connected"))},
    )
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    synced = await service.sync_board(workspace_id="repo")
    started = await service.start_todo(
        todo_id=synced.items[0].todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    await service.mark_run_completed_async(run_id=started.run_id or "")
    board = await service.sync_board(workspace_id="repo")

    assert board.items[0].status == BoardTodoStatus.DONE
    assert board.items[0].linked_pr_number == 41


@pytest.mark.asyncio
async def test_incremental_closed_issue_fetches_linked_pr_missing_from_cursor_map(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    github = _GitHubClient(
        {"owner/repo": (("Issue", 32),)},
        pull_requests={"owner/repo": (("Fix issue", 32, "2026-05-10T01:00:00Z"),)},
    )
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=github,
    )

    synced = await service.sync_board(workspace_id="repo")
    item = synced.items[0]
    await repository.update_async(
        item.model_copy(
            update={
                "status": BoardTodoStatus.REVIEW,
                "linked_pr_number": 32,
                "linked_pr_url": "https://github.com/owner/repo/pull/32",
            }
        )
    )
    github.set_issues({"owner/repo": (("Issue", 32, "closed"),)})
    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(
            workspace_id="repo",
            after_revision=synced.revision,
        )
    )
    board = await service.list_board(workspace_id="repo")

    assert github.pull_since[1] is not None
    assert github.pull_request_numbers == [32]
    assert board.items[0].status == BoardTodoStatus.DONE


@pytest.mark.asyncio
async def test_incremental_sync_fetches_linked_pr_missing_from_cursor_map(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient(
        {"owner/repo": (("Issue", 31),)},
        pull_requests={"owner/repo": (("Fix issue", 31, "2026-05-10T01:00:00Z"),)},
        timelines={"owner/repo#31": (31,)},
    )
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    synced = await service.sync_board(workspace_id="repo")
    started = await service.start_todo(
        todo_id=synced.items[0].todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    await service.mark_run_completed_async(run_id=started.run_id or "")
    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(
            workspace_id="repo",
            after_revision=synced.revision,
        )
    )
    board = await service.list_board(workspace_id="repo")

    assert github.pull_since[1] is not None
    assert github.pull_request_numbers == [31]
    assert board.items[0].status == BoardTodoStatus.DONE
    assert board.items[0].linked_pr_number == 31


@pytest.mark.asyncio
async def test_merged_pull_request_without_matching_issue_is_not_done(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient(
            {"owner/repo": (("Issue", 10),)},
            pull_requests={"owner/repo": (("Fix other", 24, "2026-05-10T09:00:00Z"),)},
        ),
    )

    board = await service.sync_board(workspace_id="repo")

    assert [
        (item.title, item.status, item.linked_pr_number) for item in board.items
    ] == [("Issue", BoardTodoStatus.TODO, None)]


@pytest.mark.asyncio
async def test_historical_untracked_pull_request_todo_is_archived(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo_pr",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Historical PR",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_PULL_REQUEST,
            source_key="github:owner/repo:pr:25",
            repository_full_name="owner/repo",
            pull_request_number=25,
            linked_pr_number=25,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        github=_GitHubClient({"owner/repo": ()}),
    )

    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert board.items[0].status == BoardTodoStatus.ARCHIVED


@pytest.mark.asyncio
async def test_archived_external_todo_and_sync_does_not_reactivate(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient({"owner/repo": (("Synced issue", 7),)}),
    )

    created = await _create_test_todo(
        service, workspace_id="repo", title="Imported issue", body=""
    )
    archived = await service.archive_todo(
        todo_id=created.todo_id,
        payload=BoardTodoArchiveRequest(reason="done elsewhere"),
    )
    board = await service.sync_board(workspace_id="repo", include_archived=True)

    assert archived.status == BoardTodoStatus.ARCHIVED
    assert {item.title: item.status for item in board.items}["Imported issue"] == (
        BoardTodoStatus.ARCHIVED
    )
    assert {item.title: item.status for item in board.items}["Synced issue"] == (
        BoardTodoStatus.TODO
    )


@pytest.mark.asyncio
async def test_delta_returns_changes_after_revision(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    first = await _create_test_todo(
        service, workspace_id="repo", title="First", body=""
    )
    board = await service.list_board(workspace_id="repo")
    second = await _create_test_todo(
        service, workspace_id="repo", title="Second", body=""
    )

    delta = await service.list_board_changes(
        workspace_id="repo",
        after_revision=board.revision,
    )

    assert first.item_revision <= board.revision
    assert [item.todo_id for item in delta.changed_items] == [second.todo_id]
    assert delta.removed_todo_ids == ()
    assert delta.revision > board.revision


@pytest.mark.asyncio
async def test_archive_delta_removes_from_active_and_restore_returns_to_todo(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service, workspace_id="repo", title="Restore", body=""
    )
    item = await service.repository_for_tests.update_async(
        item.model_copy(
            update={
                "session_id": "session-old",
                "run_id": "run-old",
                "current_attempt_id": "attempt-old",
                "active_attempt_id": "attempt-old",
                "execution_workspace_id": "workspace-old",
                "execution_policy": BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                "runtime_target_kind": BoardTodoRuntimeTargetKind.LOCAL_ROLE,
                "runtime_target_id": "role:reviewer",
                "queue_ticket_id": "queue-old",
            }
        )
    )
    board = await service.list_board(workspace_id="repo")
    archived = await service.archive_todo(
        todo_id=item.todo_id,
        payload=BoardTodoArchiveRequest(),
    )

    active_delta = await service.list_board_changes(
        workspace_id="repo",
        after_revision=board.revision,
    )
    archived_delta = await service.list_board_changes(
        workspace_id="repo",
        include_archived=True,
        after_revision=board.revision,
    )
    restored = await service.restore_todo(todo_id=item.todo_id)

    assert archived.status == BoardTodoStatus.ARCHIVED
    assert active_delta.changed_items == ()
    assert active_delta.removed_todo_ids == (item.todo_id,)
    assert [changed.todo_id for changed in archived_delta.changed_items] == [
        item.todo_id
    ]
    assert restored.status == BoardTodoStatus.TODO
    assert restored.archived_at is None
    assert restored.session_id is None
    assert restored.run_id is None
    assert restored.current_attempt_id is None
    assert restored.active_attempt_id is None
    assert restored.execution_workspace_id is None
    assert restored.execution_policy is None
    assert restored.runtime_target_kind is None
    assert restored.runtime_target_id is None
    assert restored.queue_ticket_id is None


@pytest.mark.asyncio
async def test_sync_changes_uses_github_issue_cursor(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient({"owner/repo": (("Issue", 30),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    board = await service.sync_board(workspace_id="repo")
    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(
            workspace_id="repo",
            after_revision=board.revision,
        )
    )

    assert github.issue_since[0] is None
    assert github.issue_since[1] is not None
    assert github.pull_since[0] is None
    assert github.pull_since[1] is not None


@pytest.mark.asyncio
async def test_sync_changes_uses_second_precision_cursor(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient({"owner/repo": (("Issue", 30),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    board = await service.sync_board(workspace_id="repo")
    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(
            workspace_id="repo",
            after_revision=board.revision,
        )
    )

    issue_cursor = github.issue_since[1]
    pull_cursor = github.pull_since[1]
    assert issue_cursor is not None
    assert pull_cursor is not None
    assert issue_cursor.microsecond == 0
    assert pull_cursor.microsecond == 0
    assert issue_cursor == pull_cursor


@pytest.mark.asyncio
async def test_force_full_sync_changes_ignores_github_issue_cursor(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient({"owner/repo": (("Issue", 30),)})
    service = _service(tmp_path, workspaces=(workspace,), github=github)

    board = await service.sync_board(workspace_id="repo")
    await service.sync_board_changes(
        BoardTodoSyncChangesRequest(
            workspace_id="repo",
            after_revision=board.revision,
            force_full=True,
        )
    )

    assert github.issue_since[0] is None
    assert github.issue_since[1] is None
    assert github.pull_since[0] is None
    assert github.pull_since[1] is None


@pytest.mark.asyncio
async def test_start_request_changes_and_run_completion_flow(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service, workspace_id="repo", title="Implement", body="Body"
    )

    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    reviewed = (await service.list_board(workspace_id="repo")).items[0]
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Please revise",
            final_prompt=f"{request_prompt}\n\nEdited by reviewer.",
        ),
    )

    assert started.status == BoardTodoStatus.IN_PROGRESS
    assert started.session_id is not None
    assert reviewed.status == BoardTodoStatus.REVIEW
    assert changed.status == BoardTodoStatus.IN_PROGRESS
    assert changed.run_id != started.run_id
    assert run_service.prompts[-1].endswith("Edited by reviewer.")
    attempts = await service.repository_for_tests.list_attempts_for_todo_async(
        item.todo_id
    )
    attempts_by_type = {attempt.attempt_type: attempt for attempt in attempts}
    request_attempt = attempts_by_type[BoardTodoAttemptType.REQUEST_CHANGES]
    assert BoardTodoAttemptType.START in attempts_by_type
    assert changed.current_attempt_id == request_attempt.attempt_id
    assert changed.active_attempt_id == request_attempt.attempt_id
    assert request_attempt.prompt_ref is not None
    prompt = await service.repository_for_tests.require_handoff_prompt_async(
        request_attempt.prompt_ref
    )
    assert prompt.template_kind == "request_changes"
    assert prompt.final_prompt_snapshot.endswith("Edited by reviewer.")


@pytest.mark.asyncio
async def test_queued_request_changes_uses_selected_runtime_target(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:reviewer",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    assert started.run_id is not None
    session_service.sessions[
        started.session_id or ""
    ].normal_root_role_id = "main_agent"
    run_runtime.set_status(started.run_id, RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )

    queued = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Please revise",
            final_prompt=request_prompt,
            runtime_target_id="role:reviewer",
        ),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()

    assert drained == 1
    assert run_service.intents[-1].target_role_id == "reviewer"
    assert session_service.sessions[started.session_id or ""].normal_root_role_id == (
        "main_agent"
    )


@pytest.mark.asyncio
async def test_queued_request_changes_failure_preserves_previous_run(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _ControlledRunService(run_runtime, fail_on_count=2)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    assert started.run_id == "run-1"
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )
    queued = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Please revise",
            final_prompt=request_prompt,
        ),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()
    restored = await service.repository_for_tests.require_async(item.todo_id)
    failed_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert restored.status == BoardTodoStatus.REVIEW
    assert restored.run_id == started.run_id
    assert failed_ticket.status == BoardTodoQueueStatus.FAILED


@pytest.mark.asyncio
async def test_queued_start_failure_cleans_fork_and_clears_handoff_metadata(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    workspace_service = _WorkspaceService((workspace,))
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _ControlledRunService(run_runtime, fail_on_count=1)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        workspace_service=workspace_service,
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    active = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Active issue",
    )
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "active_attempt_id": "attempt-active",
                "run_id": "run-active",
                "runtime_target_id": "role:main_agent",
            }
        )
    )
    run_runtime.records["run-active"] = RunRuntimeRecord(
        run_id="run-active",
        session_id="session-active",
        status=RunRuntimeStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Queued issue",
    )
    queued = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Queued prompt"),
    )
    assert queued.queue_ticket_id is not None
    await service.repository_for_tests.update_async(
        active.model_copy(
            update={
                "status": BoardTodoStatus.DONE,
                "active_attempt_id": None,
                "run_id": None,
            }
        )
    )

    drained = await service.drain_queue_once()
    restored = await service.repository_for_tests.require_async(item.todo_id)
    failed_ticket = await service.repository_for_tests.require_queue_ticket_async(
        queued.queue_ticket_id
    )

    assert drained == 1
    assert restored.status == BoardTodoStatus.TODO
    assert restored.current_attempt_id is None
    assert restored.active_attempt_id is None
    assert restored.execution_workspace_id is None
    assert restored.execution_policy is None
    assert restored.runtime_target_kind is None
    assert restored.runtime_target_id is None
    assert failed_ticket.status == BoardTodoQueueStatus.FAILED
    assert len(workspace_service.deleted_workspace_ids) == 1
    assert workspace_service.deleted_workspace_ids[0] != "repo"
    assert session_service.deleted_session_ids == ["session-1"]
    assert "session-1" not in session_service.sessions


@pytest.mark.asyncio
async def test_request_changes_preview_and_empty_prompt_do_not_create_handoff(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service, workspace_id="repo", title="Implement", body="Body"
    )

    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")

    preview = await service.preview_request_changes_todo(
        todo_id=item.todo_id,
        payload=BoardTodoPreviewRequestChangesRequest(
            feedback="Please revise",
            view_workspace_id="repo",
        ),
    )
    attempts_after_preview = (
        await service.repository_for_tests.list_attempts_for_todo_async(item.todo_id)
    )

    assert "Please revise" in preview.prompt
    assert preview.session_id == started.session_id
    assert preview.run_id == started.run_id
    assert run_service.count == 1
    assert len(attempts_after_preview) == 1
    with pytest.raises(ValueError, match="final_prompt is required"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise",
                final_prompt=" ",
            ),
        )

    attempts_after_reject = (
        await service.repository_for_tests.list_attempts_for_todo_async(item.todo_id)
    )
    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 1
    assert attempts_after_reject == attempts_after_preview
    assert board.items[0].status == BoardTodoStatus.REVIEW


@pytest.mark.asyncio
async def test_request_changes_derives_missing_runtime_target_from_session(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Legacy review item",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Process",
            runtime_target_id="preset:default",
        ),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    reviewed = await service.repository_for_tests.require_async(item.todo_id)
    await service.repository_for_tests.update_async(
        reviewed.model_copy(
            update={
                "runtime_target_kind": None,
                "runtime_target_id": None,
            }
        )
    )

    preview = await service.preview_request_changes_todo(
        todo_id=item.todo_id,
        payload=BoardTodoPreviewRequestChangesRequest(feedback="Revise"),
    )
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Revise",
            final_prompt=preview.prompt,
        ),
    )

    assert preview.runtime_target_id == "preset:default"
    assert (
        changed.runtime_target_kind == BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET
    )
    assert changed.runtime_target_id == "preset:default"
    assert run_service.intents[-1].session_mode == SessionMode.ORCHESTRATION


@pytest.mark.asyncio
async def test_request_changes_rejects_runtime_target_outside_session_mode(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )

    with pytest.raises(ValueError, match="normal sessions require a role"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise",
                final_prompt=request_prompt,
                runtime_target_id="preset:default",
            ),
        )

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.REVIEW
    assert board.items[0].run_id == started.run_id


@pytest.mark.asyncio
async def test_request_changes_rejects_mismatched_orchestration_preset(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Process",
            runtime_target_id="preset:plan",
        ),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )

    with pytest.raises(ValueError, match="orchestration preset"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise",
                final_prompt=request_prompt,
                runtime_target_id="preset:default",
            ),
        )

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.REVIEW
    assert board.items[0].run_id == started.run_id


@pytest.mark.asyncio
async def test_request_changes_preview_includes_existing_runtime_target_option(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(
            final_prompt="Process",
            runtime_target_id="role:reviewer",
        ),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")

    preview = await service.preview_request_changes_todo(
        todo_id=item.todo_id,
        payload=BoardTodoPreviewRequestChangesRequest(
            feedback="Please revise",
        ),
    )

    assert preview.runtime_target_id == "role:reviewer"
    assert "role:reviewer" in {
        option.target_id for option in preview.runtime_target_options
    }


@pytest.mark.asyncio
async def test_board_started_runs_inherit_general_shell_policy(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
        shell_safety_policy_enabled=False,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )

    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Please revise",
            final_prompt=request_prompt,
        ),
    )

    assert changed.run_id == "run-2"
    assert [intent.shell_safety_policy_enabled for intent in run_service.intents] == [
        False,
        False,
    ]


@pytest.mark.asyncio
async def test_mark_done_moves_review_item_to_done(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Ready",
        status=BoardTodoStatus.REVIEW,
    )

    done = await service.mark_done(
        todo_id=item.todo_id,
        payload=BoardTodoMarkDoneRequest(reason="Looks good"),
    )

    assert done.status == BoardTodoStatus.DONE
    assert done.last_status_reason == "Looks good"
    assert done.item_revision > item.item_revision


@pytest.mark.asyncio
async def test_mark_done_rejects_non_review_item(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(service, workspace_id="repo", title="Not ready")

    with pytest.raises(ValueError, match="only review board items can be marked done"):
        await service.mark_done(
            todo_id=item.todo_id,
            payload=BoardTodoMarkDoneRequest(),
        )

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.TODO


@pytest.mark.asyncio
async def test_start_rejects_non_todo_items_without_creating_another_run(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service, workspace_id="repo", title="Implement", body="Body"
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )

    with pytest.raises(ValueError, match="only todo board items can be started"):
        await service.start_todo(
            todo_id=item.todo_id,
            payload=BoardTodoStartRequest(final_prompt="Process"),
        )

    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 1
    assert board.items[0].status == BoardTodoStatus.IN_PROGRESS
    assert board.items[0].session_id == started.session_id
    assert board.items[0].run_id == started.run_id


@pytest.mark.asyncio
async def test_start_reserves_todo_before_creating_session_or_run(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    session_service = _BlockingSessionService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service, workspace_id="repo", title="Implement", body="Body"
    )

    first_start = asyncio.create_task(
        service.start_todo(
            todo_id=item.todo_id,
            payload=BoardTodoStartRequest(final_prompt="Process"),
        )
    )
    await session_service.entered.wait()

    with pytest.raises(ValueError, match="only todo board items can be started"):
        await service.start_todo(
            todo_id=item.todo_id,
            payload=BoardTodoStartRequest(final_prompt="Process"),
        )

    board = await service.list_board(workspace_id="repo")
    assert session_service.count == 1
    assert run_service.count == 0
    assert board.items[0].status == BoardTodoStatus.IN_PROGRESS
    assert board.items[0].session_id is None
    assert board.items[0].run_id is None

    session_service.release.set()
    started = await first_start

    assert run_service.count == 1
    assert started.status == BoardTodoStatus.IN_PROGRESS
    assert started.session_id == "session-1"
    assert started.run_id == "run-1"


@pytest.mark.asyncio
async def test_start_rechecks_capacity_after_reservation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    session_service = _BlockingSessionService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    first = await _create_test_todo(
        service,
        workspace_id="repo",
        title="First",
    )
    second = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Second",
    )

    first_start = asyncio.create_task(
        service.start_todo(
            todo_id=first.todo_id,
            payload=BoardTodoStartRequest(final_prompt="Process first"),
        )
    )
    await session_service.entered.wait()
    queued = await service.start_todo(
        todo_id=second.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process second"),
    )

    assert queued.status == BoardTodoStatus.IN_PROGRESS
    assert queued.queue_ticket_id is not None
    assert queued.run_id is None
    assert run_service.count == 0

    session_service.release.set()
    started = await first_start

    assert started.run_id == "run-1"
    assert run_service.count == 1


@pytest.mark.asyncio
async def test_start_failure_restore_does_not_clear_newer_reservation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Reserved again",
    )
    current = await service.repository_for_tests.update_async(
        item.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "current_attempt_id": "attempt-new",
                "active_attempt_id": "attempt-new",
                "execution_workspace_id": "repo",
                "execution_policy": BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
                "runtime_target_kind": BoardTodoRuntimeTargetKind.LOCAL_ROLE,
                "runtime_target_id": "role:main_agent",
                "last_status_reason": "Preparing newer start",
            }
        )
    )

    await service._restore_failed_start_reservation(
        current,
        current_attempt_id="attempt-old",
    )

    restored = await service.repository_for_tests.require_async(item.todo_id)
    assert restored.status == BoardTodoStatus.IN_PROGRESS
    assert restored.current_attempt_id == "attempt-new"
    assert restored.active_attempt_id == "attempt-new"
    assert restored.execution_workspace_id == "repo"
    assert restored.runtime_target_id == "role:main_agent"
    assert restored.last_status_reason == "Preparing newer start"


@pytest.mark.asyncio
async def test_start_restores_todo_when_run_creation_fails(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    workspace_service = _WorkspaceService((workspace,))
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _FailingRunService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        workspace_service=workspace_service,
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service, workspace_id="repo", title="Implement", body="Body"
    )

    with pytest.raises(RuntimeError, match="run creation failed"):
        await service.start_todo(
            todo_id=item.todo_id,
            payload=BoardTodoStartRequest(final_prompt="Process"),
        )

    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 1
    assert board.items[0].status == BoardTodoStatus.TODO
    assert board.items[0].session_id is None
    assert board.items[0].run_id is None
    assert board.items[0].last_status_reason == "Start failed"
    assert len(workspace_service.deleted_workspace_ids) == 1
    assert workspace_service.deleted_workspace_ids[0] != "repo"
    assert session_service.deleted_session_ids == ["session-1"]
    assert "session-1" not in session_service.sessions


@pytest.mark.asyncio
async def test_start_stops_run_and_deletes_session_when_run_start_fails(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    workspace_service = _WorkspaceService((workspace,))
    session_service = _SessionService()
    run_runtime = _RunRuntimeRepository()
    run_service = _EnsureFailingRunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        workspace_service=workspace_service,
        session_service=session_service,
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )

    with pytest.raises(RuntimeError, match="run start failed"):
        await service.start_todo(
            todo_id=item.todo_id,
            payload=BoardTodoStartRequest(final_prompt="Process"),
        )

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.TODO
    assert run_runtime.records["run-1"].status == RunRuntimeStatus.STOPPED
    assert session_service.deleted_session_ids == ["session-1"]
    assert "session-1" not in session_service.sessions
    assert len(workspace_service.deleted_workspace_ids) == 1


@pytest.mark.asyncio
async def test_repository_handoff_records_cover_edge_paths(tmp_path: Path) -> None:
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    now = datetime.now(tz=UTC)
    todo = await repository.create_async(
        BoardTodoItem(
            todo_id="todo_repo_handoff",
            workspace_id="repo",
            status=BoardTodoStatus.TODO,
            title="Repo handoff",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:77",
            repository_full_name="owner/repo",
            issue_number=77,
            html_url="https://github.com/owner/repo/issues/77",
            created_at=now,
            updated_at=now,
        )
    )
    attempt = await repository.create_attempt_async(
        BoardTodoAttempt(
            attempt_id="battempt_repo_handoff",
            todo_id=todo.todo_id,
            attempt_type=BoardTodoAttemptType.START,
            board_workspace_id=todo.workspace_id,
            source_workspace_id=todo.workspace_id,
            execution_workspace_id=todo.workspace_id,
            execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
            runtime_target_kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
            runtime_target_id="role:main_agent",
            created_at=now,
        )
    )
    updated_attempt = await repository.update_attempt_async(
        attempt.model_copy(update={"summary": "Started"})
    )
    assert updated_attempt.summary == "Started"
    assert await repository.require_attempt_async(attempt.attempt_id) == updated_attempt
    with pytest.raises(KeyError):
        await repository.require_attempt_async("battempt_missing")
    with pytest.raises(KeyError):
        await repository.update_attempt_async(
            attempt.model_copy(update={"attempt_id": "battempt_missing"})
        )

    prompt = await repository.create_handoff_prompt_async(
        BoardTodoHandoffPrompt(
            prompt_ref="bprompt_repo_handoff",
            todo_id=todo.todo_id,
            attempt_id=attempt.attempt_id,
            template_kind=BoardTodoHandoffTemplateKind.START.value,
            template_source="built_in:start",
            final_prompt_snapshot="Please process this TODO.",
            created_at=now,
        )
    )
    stored_prompt = await repository.require_handoff_prompt_async(prompt.prompt_ref)
    assert stored_prompt.final_prompt_snapshot == "Please process this TODO."
    with pytest.raises(KeyError):
        await repository.require_handoff_prompt_async("bprompt_missing")

    ticket = await repository.create_queue_ticket_async(
        BoardTodoExecutionQueueTicket(
            queue_ticket_id="bqueue_repo_handoff",
            todo_id=todo.todo_id,
            attempt_id=attempt.attempt_id,
            prompt_ref=prompt.prompt_ref,
            queue_kind=BoardTodoQueueKind.START,
            board_workspace_id=todo.workspace_id,
            source_workspace_id=todo.workspace_id,
            execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
            runtime_target_kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
            runtime_target_id="role:main_agent",
            session_mode=SessionMode.NORMAL,
            normal_root_role_id="main_agent",
            created_at=now,
            updated_at=now,
        )
    )
    pending = await repository.list_pending_queue_tickets_async(limit=10)
    assert tuple(entry.queue_ticket_id for entry in pending) == (
        ticket.queue_ticket_id,
    )
    assert (
        await repository.renew_queue_ticket_claim_async(
            ticket=ticket,
            claim_expires_at=now + timedelta(minutes=5),
        )
        is None
    )
    claimed = await repository.claim_queue_ticket_async(
        ticket=ticket,
        claim_token="claim-1",
        claim_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        claimed_by="worker-1",
        now=datetime.now(tz=UTC),
    )
    assert claimed is not None
    assert claimed.claim_token == "claim-1"
    assert (
        await repository.claim_queue_ticket_async(
            ticket=claimed,
            claim_token="claim-2",
            claim_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
            claimed_by="worker-2",
            now=datetime.now(tz=UTC),
        )
        is None
    )
    renewed = await repository.renew_queue_ticket_claim_async(
        ticket=claimed,
        claim_expires_at=datetime.now(tz=UTC) + timedelta(minutes=10),
    )
    assert renewed is not None
    assert renewed.claim_token == "claim-1"
    assert (
        await repository.renew_queue_ticket_claim_async(
            ticket=claimed.model_copy(update={"claim_token": "stale-claim"}),
            claim_expires_at=datetime.now(tz=UTC) + timedelta(minutes=15),
        )
        is None
    )
    failed_claim = await repository.update_claimed_queue_ticket_async(
        renewed.model_copy(update={"status": BoardTodoQueueStatus.FAILED})
    )
    assert failed_claim is not None
    assert failed_claim.status == BoardTodoQueueStatus.FAILED
    assert (
        await repository.update_claimed_queue_ticket_async(
            renewed.model_copy(
                update={
                    "status": BoardTodoQueueStatus.FAILED,
                    "claim_token": "stale-claim",
                }
            )
        )
        is None
    )
    release_ticket = await repository.create_queue_ticket_async(
        ticket.model_copy(
            update={
                "queue_ticket_id": "bqueue_repo_release",
                "status": BoardTodoQueueStatus.PENDING,
                "claim_token": None,
                "claim_expires_at": None,
                "claimed_by": None,
            }
        )
    )
    release_claimed = await repository.claim_queue_ticket_async(
        ticket=release_ticket,
        claim_token="claim-release",
        claim_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        claimed_by="worker-release",
        now=datetime.now(tz=UTC),
    )
    assert release_claimed is not None
    released = await repository.release_queue_ticket_claim_async(release_claimed)
    assert released is not None
    assert released.status == BoardTodoQueueStatus.PENDING
    assert released.claim_token is None
    assert released.claim_expires_at is None
    assert await repository.release_queue_ticket_claim_async(released) is None
    assert (
        await repository.release_queue_ticket_claim_async(
            release_claimed.model_copy(update={"claim_token": "stale-release"})
        )
        is None
    )
    with pytest.raises(KeyError):
        await repository.require_queue_ticket_async("bqueue_missing")
    with pytest.raises(KeyError):
        await repository.update_queue_ticket_async(
            ticket.model_copy(update={"queue_ticket_id": "bqueue_missing"})
        )

    expired_ticket = await repository.create_queue_ticket_async(
        ticket.model_copy(
            update={
                "queue_ticket_id": "bqueue_repo_expired",
                "status": BoardTodoQueueStatus.CLAIMED,
                "claim_token": "claim-expired",
                "claim_expires_at": datetime.now(tz=UTC) - timedelta(minutes=1),
                "claimed_by": "worker-expired",
            }
        )
    )
    pending_after_claim = await repository.list_pending_queue_tickets_async(limit=10)
    assert {entry.queue_ticket_id for entry in pending_after_claim} == {
        release_ticket.queue_ticket_id,
        expired_ticket.queue_ticket_id,
    }

    workspace_template = await repository.upsert_handoff_template_async(
        BoardTodoHandoffTemplate(
            template_id="btemplate_workspace_repo",
            workspace_id=todo.workspace_id,
            scope=BoardTodoTemplateScope.WORKSPACE,
            template_kind=BoardTodoHandoffTemplateKind.START,
            template="Workspace template",
            created_at=now,
            updated_at=now,
        )
    )
    source_template = await repository.upsert_handoff_template_async(
        BoardTodoHandoffTemplate(
            template_id="btemplate_source_repo",
            workspace_id=todo.workspace_id,
            scope=BoardTodoTemplateScope.SOURCE,
            source_id="bsrc_repo",
            template_kind=BoardTodoHandoffTemplateKind.START,
            template="Source template",
            created_at=now,
            updated_at=now,
        )
    )
    assert (
        await repository.get_handoff_template_async(
            workspace_id=todo.workspace_id,
            template_kind=BoardTodoHandoffTemplateKind.START,
            source_id="bsrc_repo",
        )
    ) == source_template
    assert (
        await repository.get_handoff_template_async(
            workspace_id=todo.workspace_id,
            template_kind=BoardTodoHandoffTemplateKind.START,
            source_id="bsrc_missing",
        )
    ) == workspace_template
    assert (
        await repository.require_handoff_template_async(source_template.template_id)
        == source_template
    )
    assert (
        len(
            await repository.list_handoff_templates_async(
                workspace_id=todo.workspace_id
            )
        )
        == 2
    )
    await repository.delete_handoff_template_async(
        template_id=source_template.template_id
    )
    with pytest.raises(KeyError):
        await repository.require_handoff_template_async(source_template.template_id)
    with pytest.raises(KeyError):
        await repository.delete_handoff_template_async(template_id="btemplate_missing")


def test_board_todo_repository_handoff_row_helpers_tolerate_dirty_data() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    def select_row(sql: str) -> sqlite3.Row:
        selected = conn.execute(sql).fetchone()
        if selected is None:
            raise AssertionError("expected one sqlite row")
        return cast(sqlite3.Row, selected)

    assert (
        _row_to_attempt_or_none(
            select_row(
                """
                SELECT
                    '' AS attempt_id,
                    'todo_repo' AS todo_id,
                    'start' AS attempt_type,
                    'pending' AS status,
                    NULL AS board_workspace_id,
                    NULL AS initiated_from_workspace_id,
                    NULL AS source_workspace_id,
                    NULL AS execution_workspace_id,
                    NULL AS execution_policy,
                    NULL AS runtime_target_kind,
                    NULL AS runtime_target_id,
                    NULL AS queue_ticket_id,
                    NULL AS handoff_initiator,
                    NULL AS start_policy,
                    NULL AS yolo,
                    'not-json' AS thinking_json,
                    NULL AS session_id,
                    NULL AS run_id,
                    NULL AS prompt_ref,
                    NULL AS summary,
                    NULL AS error,
                    NULL AS created_at,
                    NULL AS started_at,
                    NULL AS finished_at
                """
            )
        )
        is None
    )
    assert (
        _row_to_handoff_prompt_or_none(
            select_row(
                """
                SELECT
                    '' AS prompt_ref,
                    'todo_repo' AS todo_id,
                    'battempt_repo' AS attempt_id,
                    'start' AS template_kind,
                    'built_in:start' AS template_source,
                    'Prompt' AS final_prompt_snapshot,
                    NULL AS created_at
                """
            )
        )
        is None
    )
    assert _row_to_queue_ticket_or_none(None) is None
    assert (
        _row_to_queue_ticket_or_none(
            select_row(
                """
                SELECT
                    '' AS queue_ticket_id,
                    'todo_repo' AS todo_id,
                    'battempt_repo' AS attempt_id,
                    'bprompt_repo' AS prompt_ref,
                    'start' AS queue_kind,
                    'pending' AS status,
                    'repo' AS board_workspace_id,
                    'repo' AS source_workspace_id,
                    NULL AS initiated_from_workspace_id,
                    NULL AS execution_workspace_id,
                    NULL AS previous_run_id,
                    'current_workspace' AS execution_policy,
                    NULL AS runtime_target_kind,
                    NULL AS runtime_target_id,
                    NULL AS session_mode,
                    NULL AS normal_root_role_id,
                    NULL AS orchestration_preset_id,
                    NULL AS yolo,
                    '[]' AS thinking_json,
                    NULL AS claim_token,
                    NULL AS claim_expires_at,
                    NULL AS claimed_by,
                    NULL AS failure_count,
                    'not-json' AS diagnostics_json,
                    NULL AS created_at,
                    NULL AS updated_at
                """
            )
        )
        is None
    )
    assert (
        _row_to_handoff_template_or_none(
            select_row(
                """
                SELECT
                    '' AS template_id,
                    'repo' AS workspace_id,
                    'workspace' AS scope,
                    'start' AS template_kind,
                    NULL AS source_id,
                    'Template' AS template,
                    NULL AS created_at,
                    NULL AS updated_at
                """
            )
        )
        is None
    )

    assert isinstance(_thinking_from_json("not-json"), RunThinkingConfig)
    assert isinstance(_thinking_from_json("[]"), RunThinkingConfig)
    assert isinstance(_thinking_from_json('{"effort":"invalid"}'), RunThinkingConfig)
    assert _diagnostics_from_json("not-json") == ()
    assert _diagnostics_from_json('"not-a-list"') == ()
    assert _diagnostics_from_json('["kept", "", 1]') == ("kept", "1")
    assert _execution_policy_or_none(None) is None
    assert _execution_policy_or_none("invalid") is None
    assert (
        _execution_policy_or_none("current_workspace")
        == BoardTodoExecutionPolicy.CURRENT_WORKSPACE
    )
    assert _runtime_target_kind_or_none(None) is None
    assert _runtime_target_kind_or_none("invalid") is None
    assert (
        _runtime_target_kind_or_none("local_role")
        == BoardTodoRuntimeTargetKind.LOCAL_ROLE
    )
    assert _session_mode_or_none(None) is None
    assert _session_mode_or_none("invalid") is None
    assert _session_mode_or_none("normal") == SessionMode.NORMAL

    ticket = BoardTodoExecutionQueueTicket(
        queue_ticket_id="bqueue_helper",
        todo_id="todo_helper",
        attempt_id="battempt_helper",
        prompt_ref="bprompt_helper",
        queue_kind=BoardTodoQueueKind.START,
        board_workspace_id="repo",
        source_workspace_id="repo",
        status=BoardTodoQueueStatus.COMPLETED,
    )
    assert (
        _queue_ticket_is_pending_or_expired(ticket, now=datetime.now(tz=UTC)) is False
    )
    assert (
        _queue_ticket_is_pending_or_expired(
            ticket.model_copy(update={"status": BoardTodoQueueStatus.CLAIMED}),
            now=datetime.now(tz=UTC),
        )
        is False
    )


@pytest.mark.asyncio
async def test_repository_start_reservation_rejects_stale_todo(
    tmp_path: Path,
) -> None:
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    now = _now()
    item = BoardTodoItem(
        todo_id="todo-1",
        workspace_id="repo",
        status=BoardTodoStatus.TODO,
        title="Implement",
        body="Body",
        source_provider=BoardTodoSourceProvider.GITHUB,
        source_type=BoardTodoSourceType.GITHUB_ISSUE,
        source_key="github:owner/repo:issue:1",
        repository_full_name="owner/repo",
        issue_number=1,
        created_at=now,
        updated_at=now,
    )
    created = await repository.create_async(item)

    reserved = await repository.reserve_start_async(created)
    with pytest.raises(ValueError, match="only todo board items can be started"):
        await repository.reserve_start_async(created)

    current = await repository.require_async(created.todo_id)
    assert reserved.status == BoardTodoStatus.IN_PROGRESS
    assert current.status == BoardTodoStatus.IN_PROGRESS
    assert current.session_id is None
    assert current.run_id is None


@pytest.mark.asyncio
async def test_request_changes_rejects_non_review_items_without_creating_another_run(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _RunService(run_runtime)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(
            feedback="Please revise",
            final_prompt=request_prompt,
        ),
    )

    with pytest.raises(ValueError, match="only review board items can request changes"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise again",
                final_prompt="Revise again",
            ),
        )

    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 2
    assert board.items[0].status == BoardTodoStatus.IN_PROGRESS
    assert board.items[0].session_id == changed.session_id
    assert board.items[0].run_id == changed.run_id


@pytest.mark.asyncio
async def test_request_changes_reserves_review_before_creating_follow_up_run(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _ControlledRunService(run_runtime, block_on_count=2)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )

    first_change = asyncio.create_task(
        service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise",
                final_prompt=request_prompt,
            ),
        )
    )
    await run_service.entered.wait()

    with pytest.raises(ValueError, match="only review board items can request changes"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise again",
                final_prompt="Revise again",
            ),
        )

    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 2
    assert board.items[0].status == BoardTodoStatus.IN_PROGRESS
    assert board.items[0].session_id == started.session_id
    assert board.items[0].run_id is None
    assert board.items[0].last_status_reason == "Preparing board todo request changes"

    run_service.release.set()
    changed = await first_change

    assert run_service.count == 2
    assert changed.status == BoardTodoStatus.IN_PROGRESS
    assert changed.session_id == started.session_id
    assert changed.run_id == "run-2"
    assert run_service.prompts[-1] == request_prompt


@pytest.mark.asyncio
async def test_request_changes_restores_review_when_run_creation_fails(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _ControlledRunService(run_runtime, fail_on_count=2)
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        run_service=run_service,
        run_runtime=run_runtime,
    )
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Implement",
        body="Body",
    )
    started = await service.start_todo(
        todo_id=item.todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )
    run_runtime.set_status(started.run_id or "", RunRuntimeStatus.COMPLETED)
    await service.reconcile_workspace_async(workspace_id="repo")
    request_prompt = await _preview_request_changes_prompt(
        service,
        todo_id=item.todo_id,
    )

    with pytest.raises(RuntimeError, match="run creation failed"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(
                feedback="Please revise",
                final_prompt=request_prompt,
            ),
        )

    board = await service.list_board(workspace_id="repo")
    attempts = await service.repository_for_tests.list_attempts_for_todo_async(
        item.todo_id
    )
    assert run_service.count == 2
    assert board.items[0].status == BoardTodoStatus.REVIEW
    assert board.items[0].session_id == started.session_id
    assert board.items[0].run_id == started.run_id
    assert board.items[0].last_status_reason == "Request changes failed"
    request_attempt = next(
        attempt
        for attempt in attempts
        if attempt.attempt_type == BoardTodoAttemptType.REQUEST_CHANGES
    )
    assert request_attempt.status == BoardTodoAttemptStatus.FAILED
    assert request_attempt.error == "run creation failed"


@pytest.mark.asyncio
async def test_repository_request_changes_reservation_rejects_stale_review(
    tmp_path: Path,
) -> None:
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    now = _now()
    item = BoardTodoItem(
        todo_id="todo-1",
        workspace_id="repo",
        status=BoardTodoStatus.REVIEW,
        title="Implement",
        body="Body",
        source_provider=BoardTodoSourceProvider.GITHUB,
        source_type=BoardTodoSourceType.GITHUB_ISSUE,
        source_key="github:owner/repo:issue:1",
        repository_full_name="owner/repo",
        issue_number=1,
        session_id="session-1",
        run_id="run-1",
        created_at=now,
        updated_at=now,
    )
    created = await repository.create_async(item)

    reserved = await repository.reserve_request_changes_async(created)
    with pytest.raises(ValueError, match="only review board items can request changes"):
        await repository.reserve_request_changes_async(created)

    current = await repository.require_async(created.todo_id)
    assert reserved.status == BoardTodoStatus.IN_PROGRESS
    assert current.status == BoardTodoStatus.IN_PROGRESS
    assert current.session_id == "session-1"
    assert current.run_id is None


@pytest.mark.asyncio
async def test_deleted_session_returns_active_todo_to_todo(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        github=_GitHubClient({"owner/repo": (("Issue", 31),)}),
    )
    synced = await service.sync_board(workspace_id="repo")
    started = await service.start_todo(
        todo_id=synced.items[0].todo_id,
        payload=BoardTodoStartRequest(final_prompt="Process"),
    )

    await service.mark_session_deleted_async(session_id=started.session_id or "")

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.TODO
    assert board.items[0].session_id is None
    assert board.items[0].run_id is None
    assert board.items[0].last_status_reason == "Bound session deleted"


@pytest.mark.asyncio
async def test_deleted_session_keeps_done_todo_done_but_clears_session(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(tmp_path, workspaces=(workspace,), repository=repository)
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-done",
            workspace_id="repo",
            status=BoardTodoStatus.DONE,
            title="Done",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:201",
            repository_full_name="owner/repo",
            issue_number=201,
            session_id="session-done",
            run_id="run-done",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    await service.mark_session_deleted_async(session_id="session-done")

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.DONE
    assert board.items[0].session_id is None
    assert board.items[0].run_id is None


@pytest.mark.asyncio
async def test_reconcile_returns_in_progress_with_missing_run_to_todo(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    service = _service(tmp_path, workspaces=(workspace,), repository=repository)
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-missing-run",
            workspace_id="repo",
            status=BoardTodoStatus.IN_PROGRESS,
            title="Missing run",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:202",
            repository_full_name="owner/repo",
            issue_number=202,
            session_id="session-missing",
            run_id="run-missing",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    await service.reconcile_workspace_async(workspace_id="repo")

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.TODO
    assert board.items[0].session_id is None
    assert board.items[0].run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_status",
    (RunRuntimeStatus.FAILED, RunRuntimeStatus.STOPPED),
)
async def test_reconcile_keeps_bound_terminal_runs_in_progress(
    tmp_path: Path,
    runtime_status: RunRuntimeStatus,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    repository = BoardTodoRepository(tmp_path / "board-todos.sqlite")
    run_runtime = _RunRuntimeRepository()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
        repository=repository,
        run_runtime=run_runtime,
    )
    run_runtime.records["run-terminal"] = RunRuntimeRecord(
        run_id="run-terminal",
        session_id="session-terminal",
        status=runtime_status,
        created_at=_now(),
        updated_at=_now(),
    )
    await repository.create_async(
        BoardTodoItem(
            todo_id="todo-terminal-run",
            workspace_id="repo",
            status=BoardTodoStatus.IN_PROGRESS,
            title="Terminal run",
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key="github:owner/repo:issue:203",
            repository_full_name="owner/repo",
            issue_number=203,
            session_id="session-terminal",
            run_id="run-terminal",
            created_at=_now(),
            updated_at=_now(),
        )
    )

    await service.reconcile_workspace_async(workspace_id="repo")

    board = await service.list_board(workspace_id="repo")
    assert board.items[0].status == BoardTodoStatus.IN_PROGRESS
    assert board.items[0].session_id == "session-terminal"
    assert board.items[0].run_id == "run-terminal"
    assert board.items[0].run_status == runtime_status.value


@pytest.mark.asyncio
async def test_linked_pr_merge_marks_done(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="",
    )
    linked = await service.link_pull_request(
        todo_id=item.todo_id,
        payload=BoardTodoLinkPullRequestRequest(pull_request_number=12),
    )

    await service.mark_github_pull_request_merged_async(
        repository_full_name="owner/repo",
        pull_request_number=12,
    )
    board = await service.list_board(workspace_id="repo")

    assert linked.linked_pr_number == 12
    assert board.items[0].status == BoardTodoStatus.DONE


@pytest.mark.asyncio
async def test_linked_pr_merge_matches_repository_case_insensitively(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "Owner/Repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="",
        repository_full_name="owner/repo",
    )
    await service.link_pull_request(
        todo_id=item.todo_id,
        payload=BoardTodoLinkPullRequestRequest(pull_request_number=12),
    )

    await service.mark_github_pull_request_merged_async(
        repository_full_name="Owner/Repo",
        pull_request_number=12,
    )
    board = await service.list_board(workspace_id="repo")

    assert board.items[0].status == BoardTodoStatus.DONE


@pytest.mark.asyncio
async def test_link_pr_marks_done_when_pull_request_already_merged(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    github = _GitHubClient(
        pull_requests={"owner/repo": (("Merged PR", 12, "2026-05-10T09:00:00Z"),)}
    )
    service = _service(tmp_path, workspaces=(workspace,), github=github)
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="",
    )

    linked = await service.link_pull_request(
        todo_id=item.todo_id,
        payload=BoardTodoLinkPullRequestRequest(pull_request_number=12),
    )

    assert linked.status == BoardTodoStatus.DONE
    assert linked.linked_pr_number == 12
    assert linked.linked_pr_url == "https://github.com/owner/repo/pull/12"
    assert linked.last_status_reason == "Linked GitHub pull request merged"
    assert github.pull_request_numbers == [12]


@pytest.mark.asyncio
async def test_link_pr_url_matches_legacy_repository_case_insensitively(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo", "Owner/Repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="",
        repository_full_name="Owner/Repo",
    )

    linked = await service.link_pull_request(
        todo_id=item.todo_id,
        payload=BoardTodoLinkPullRequestRequest(
            pull_request_number=12,
            pull_request_url="https://github.com/owner/repo/pull/12",
        ),
    )

    assert linked.repository_full_name == "owner/repo"
    assert linked.linked_pr_number == 12


@pytest.mark.asyncio
async def test_link_pr_rejects_malformed_pull_request_url(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    service = _service(tmp_path, workspaces=(workspace,))
    item = await _create_test_todo(
        service,
        workspace_id="repo",
        title="Imported issue",
        body="",
    )

    with pytest.raises(ValueError, match="GitHub pull request URL"):
        await service.link_pull_request(
            todo_id=item.todo_id,
            payload=BoardTodoLinkPullRequestRequest(
                pull_request_number=12,
                pull_request_url="https://github.com/owner/repo/pull/999",
            ),
        )


@pytest.mark.asyncio
async def test_board_todo_service_protocol_stubs_raise_not_implemented() -> None:
    workspace_service = cast(WorkspaceServiceLike, object())
    trigger_service = cast(GitHubTriggerServiceLike, object())
    github_client = cast(GitHubApiClientLike, object())
    session_service = cast(SessionServiceLike, object())
    run_service = cast(SessionRunServiceLike, object())
    runtime_repo = cast(RunRuntimeRepositoryLike, object())

    with pytest.raises(NotImplementedError):
        method = cast(
            _GetWorkspaceStub,
            getattr(WorkspaceServiceLike, "get_workspace_async"),
        )
        await method(workspace_service, "workspace")
    with pytest.raises(NotImplementedError):
        method = cast(
            _ForkWorkspaceStub,
            getattr(WorkspaceServiceLike, "fork_workspace_async"),
        )
        await method(workspace_service, "workspace", name="fork")
    with pytest.raises(NotImplementedError):
        method = cast(
            _DeleteWorkspaceStub,
            getattr(WorkspaceServiceLike, "delete_workspace_with_options_async"),
        )
        await method(workspace_service, workspace_id="fork")
    with pytest.raises(NotImplementedError):
        method = cast(
            _ListAccountsStub,
            getattr(GitHubTriggerServiceLike, "list_accounts_async"),
        )
        await method(trigger_service)
    with pytest.raises(NotImplementedError):
        method = cast(
            _ResolveAccountTokenStub,
            getattr(GitHubTriggerServiceLike, "resolve_account_token_async"),
        )
        await method(trigger_service, "account")
    with pytest.raises(NotImplementedError):
        method = cast(
            _ListRepositoryIssuesStub,
            getattr(GitHubApiClientLike, "list_repository_issues"),
        )
        await method(
            github_client,
            token="token",
            owner="owner",
            repo="repo",
        )
    with pytest.raises(NotImplementedError):
        method = cast(
            _ListRepositoryPullRequestsStub,
            getattr(GitHubApiClientLike, "list_repository_pull_requests"),
        )
        await method(
            github_client,
            token="token",
            owner="owner",
            repo="repo",
        )
    with pytest.raises(NotImplementedError):
        method = cast(
            _GetRepositoryPullRequestStub,
            getattr(GitHubApiClientLike, "get_repository_pull_request"),
        )
        await method(
            github_client,
            token="token",
            owner="owner",
            repo="repo",
            pull_request_number=1,
        )
    with pytest.raises(NotImplementedError):
        method = cast(
            _ListIssueTimelineEventsStub,
            getattr(GitHubApiClientLike, "list_issue_timeline_events"),
        )
        await method(
            github_client,
            token="token",
            owner="owner",
            repo="repo",
            issue_number=1,
        )
    with pytest.raises(NotImplementedError):
        method = cast(
            _CreateSessionStub,
            getattr(SessionServiceLike, "create_session_async"),
        )
        await method(
            session_service,
            workspace_id="workspace",
        )
    with pytest.raises(NotImplementedError):
        method = cast(
            _GetSessionStub,
            getattr(SessionServiceLike, "get_session_async"),
        )
        await method(session_service, "session")
    with pytest.raises(NotImplementedError):
        method = cast(
            _DeleteSessionStub,
            getattr(SessionServiceLike, "delete_session_async"),
        )
        await method(session_service, "session")
    intent = IntentInput(
        session_id="session",
        input=(),
        display_input=(),
        yolo=True,
    )
    with pytest.raises(NotImplementedError):
        method = cast(
            _CreateRunStub,
            getattr(SessionRunServiceLike, "create_run_async"),
        )
        await method(run_service, intent)
    with pytest.raises(NotImplementedError):
        method = cast(
            _EnsureRunStartedStub,
            getattr(SessionRunServiceLike, "ensure_run_started_async"),
        )
        await method(run_service, "run")
    with pytest.raises(NotImplementedError):
        method = cast(
            _StopRunStub,
            getattr(SessionRunServiceLike, "stop_run_async"),
        )
        await method(run_service, "run")
    with pytest.raises(NotImplementedError):
        method = cast(
            _GetRuntimeStub,
            getattr(RunRuntimeRepositoryLike, "get_async"),
        )
        await method(runtime_repo, "run")


def test_board_todo_service_github_helper_edge_cases() -> None:
    assert _parse_github_remote("git@github.com:owner/repo.git") == "owner/repo"
    assert _parse_github_remote("https://github.com/owner/repo.git") == "owner/repo"
    assert _parse_github_remote("https://example.test/owner/repo") is None
    assert _parse_github_remote("https://github.com/owner") is None
    assert _parse_github_remote("https://github.com//repo") is None
    assert (
        _first_github_remote_url(
            "mirror https://example.test/owner/repo (fetch)\n"
            "coolplayagent https://github.com/owner/repo.git (fetch)\n"
        )
        == "https://github.com/owner/repo.git"
    )

    assert _parse_github_pull_request_url(None, 12) is None
    assert (
        _parse_github_pull_request_url("https://example.test/owner/repo/pull/12", 12)
        is None
    )
    assert (
        _parse_github_pull_request_url("https://github.com/owner/repo/issues/12", 12)
        is None
    )
    assert (
        _parse_github_pull_request_url("https://github.com/owner/repo/pull/not-int", 12)
        is None
    )
    assert (
        _parse_github_pull_request_url("https://github.com/owner/repo/pull/13", 12)
        is None
    )
    assert (
        _parse_github_pull_request_url("https://github.com/owner/repo/pull/12", 12)
        == "owner/repo"
    )

    assert _json_int(True) is None
    assert _json_int(None) is None
    assert _json_int("17") == 17
    assert _json_int("not-int") is None
    assert _json_int(18.2) is None
    assert _json_datetime_or_none("") is None
    assert _json_datetime_or_none("not-a-date") is None

    assert (
        _format_github_sync_error(
            error=GitHubApiError(message="", status_code=403),
            repository_full_name="owner/repo",
            force_full=True,
        )
        == "GitHub sync failed for owner/repo (full status=403): "
        "GitHub sync failed with status 403"
    )
    assert (
        _format_github_sync_error(
            error=GitHubApiError(message=""),
            repository_full_name="owner/repo",
            force_full=False,
        )
        == "GitHub sync failed for owner/repo (incremental): "
        "GitHub sync failed before response"
    )


def test_board_todo_handoff_helper_edge_cases() -> None:
    item = BoardTodoItem(
        todo_id="todo-helper",
        workspace_id="repo",
        title="Helper",
        source_provider=BoardTodoSourceProvider.GITHUB,
        source_type=BoardTodoSourceType.GITHUB_ISSUE,
        source_key="github:owner/repo:issue:1",
        repository_full_name="owner/repo",
        issue_number=1,
        execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
    )
    assert _default_execution_policy(item) == BoardTodoExecutionPolicy.CURRENT_WORKSPACE
    item_without_policy = item.model_copy(update={"execution_policy": None})
    assert (
        _default_execution_policy(item_without_policy)
        == BoardTodoExecutionPolicy.FORK_GIT_WORKTREE
    )

    current_preview = _execution_workspace_preview(
        item=item,
        scope=BoardTodoScope(
            board_workspace_id="repo",
            view_workspace_id="fork-view",
        ),
        execution_policy=BoardTodoExecutionPolicy.CURRENT_WORKSPACE,
    )
    fork_preview = _execution_workspace_preview(
        item=item,
        scope=BoardTodoScope(
            board_workspace_id="repo",
            view_workspace_id="fork-view",
        ),
        execution_policy=BoardTodoExecutionPolicy.FORK_GIT_WORKTREE,
    )
    assert current_preview.workspace_id == "fork-view"
    assert fork_preview.workspace_id is None

    with pytest.raises(ValueError, match="preset id"):
        _resolve_runtime_target(
            runtime_target_id="preset:",
            session_mode=None,
            normal_root_role_id=None,
            orchestration_preset_id=None,
        )
    with pytest.raises(ValueError, match="role id"):
        _resolve_runtime_target(
            runtime_target_id="role:",
            session_mode=None,
            normal_root_role_id=None,
            orchestration_preset_id=None,
        )
    with pytest.raises(ValueError, match="role: or preset:"):
        _resolve_runtime_target(
            runtime_target_id="reviewer",
            session_mode=None,
            normal_root_role_id=None,
            orchestration_preset_id=None,
        )
    assert (
        _resolve_runtime_target(
            runtime_target_id=None,
            session_mode=None,
            normal_root_role_id=None,
            orchestration_preset_id=None,
            fallback_runtime_target_kind=BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET,
        ).target_id
        == "preset:default"
    )
    assert (
        _resolve_runtime_target(
            runtime_target_id=None,
            session_mode=None,
            normal_root_role_id=None,
            orchestration_preset_id=None,
            fallback_runtime_target_kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
        ).target_id
        == "role:main_agent"
    )

    preset_target = BoardTodoRuntimeTargetOption(
        target_id="preset:default",
        kind=BoardTodoRuntimeTargetKind.ORCHESTRATION_PRESET,
        label="Default",
    )
    role_target = BoardTodoRuntimeTargetOption(
        target_id="role:main_agent",
        kind=BoardTodoRuntimeTargetKind.LOCAL_ROLE,
        label="Main",
    )
    with pytest.raises(ValueError, match="orchestration mode"):
        _start_session_topology(
            runtime_target=preset_target,
            session_mode=SessionMode.NORMAL,
            normal_root_role_id=None,
            orchestration_preset_id=None,
        )
    with pytest.raises(ValueError, match="normal mode"):
        _start_session_topology(
            runtime_target=role_target,
            session_mode=SessionMode.ORCHESTRATION,
            normal_root_role_id=None,
            orchestration_preset_id=None,
        )
    with pytest.raises(ValueError, match="normal root role"):
        _start_session_topology(
            runtime_target=role_target,
            session_mode=SessionMode.NORMAL,
            normal_root_role_id="reviewer",
            orchestration_preset_id=None,
        )
    with pytest.raises(ValueError, match="orchestration preset"):
        _start_session_topology(
            runtime_target=preset_target,
            session_mode=SessionMode.ORCHESTRATION,
            normal_root_role_id=None,
            orchestration_preset_id="planning",
        )
    assert _start_session_topology(
        runtime_target=preset_target,
        session_mode=None,
        normal_root_role_id=None,
        orchestration_preset_id=None,
    ) == (SessionMode.ORCHESTRATION, None, "default")

    normal_session = SessionRecord(
        session_id="session-normal",
        workspace_id="repo",
        metadata={},
        session_mode=SessionMode.NORMAL,
        normal_root_role_id="main_agent",
        created_at=_now(),
        updated_at=_now(),
    )
    orchestration_session = normal_session.model_copy(
        update={
            "session_id": "session-orchestration",
            "session_mode": SessionMode.ORCHESTRATION,
            "normal_root_role_id": None,
        }
    )
    with pytest.raises(ValueError, match="preset runtime target"):
        _validate_runtime_target_matches_session(
            runtime_target=role_target,
            session=orchestration_session,
        )
    with pytest.raises(ValueError, match="role runtime target"):
        _validate_runtime_target_matches_session(
            runtime_target=preset_target,
            session=normal_session,
        )
    with pytest.raises(ValueError, match="orchestration preset"):
        _validate_runtime_target_matches_session(
            runtime_target=preset_target,
            session=orchestration_session.model_copy(
                update={"orchestration_preset_id": "planning"}
            ),
        )

    pending_ticket = BoardTodoExecutionQueueTicket(
        queue_ticket_id="ticket-pending",
        todo_id="todo-helper",
        attempt_id="attempt-helper",
        prompt_ref="prompt-helper",
        queue_kind=BoardTodoQueueKind.START,
        board_workspace_id="repo",
        source_workspace_id="repo",
    )
    expired_ticket = pending_ticket.model_copy(
        update={
            "queue_ticket_id": "ticket-expired",
            "status": BoardTodoQueueStatus.CLAIMED,
            "claim_expires_at": _now() - timedelta(seconds=1),
        }
    )
    live_ticket = pending_ticket.model_copy(
        update={
            "queue_ticket_id": "ticket-live",
            "status": BoardTodoQueueStatus.CLAIMED,
            "claim_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
        }
    )
    no_expiry_ticket = pending_ticket.model_copy(
        update={
            "queue_ticket_id": "ticket-no-expiry",
            "status": BoardTodoQueueStatus.CLAIMED,
            "claim_expires_at": None,
        }
    )
    failed_ticket = pending_ticket.model_copy(
        update={
            "queue_ticket_id": "ticket-failed",
            "status": BoardTodoQueueStatus.FAILED,
        }
    )
    assert _queue_ticket_can_be_claimed(pending_ticket) is True
    assert _queue_ticket_can_be_claimed(expired_ticket) is True
    assert _queue_ticket_can_be_claimed(live_ticket) is False
    assert _queue_ticket_can_be_claimed(no_expiry_ticket) is False
    assert _queue_ticket_can_be_claimed(failed_ticket) is False
    later_ticket = pending_ticket.model_copy(
        update={
            "queue_ticket_id": "ticket-later",
            "created_at": pending_ticket.created_at + timedelta(seconds=1),
        }
    )
    assert _queue_ticket_sorts_after(later_ticket, pending_ticket) is True
    assert _queue_ticket_sorts_after(pending_ticket, later_ticket) is False

    assert _role_id_from_runtime_target(None) is None
    assert (
        _target_role_id_for_run(
            session=orchestration_session,
            ticket=pending_ticket,
        )
        is None
    )
    assert (
        _target_role_id_for_run(
            session=normal_session,
            ticket=pending_ticket.model_copy(
                update={"runtime_target_id": "role:reviewer"}
            ),
        )
        == "reviewer"
    )


def test_board_todo_service_linked_pull_request_event_selection() -> None:
    pull_request_map = {
        2: {
            "number": 2,
            "html_url": "https://github.com/owner/repo/pull/2",
            "merged": True,
        }
    }
    events = (
        {"event": "cross-referenced", "issue": {"number": 1}},
        {
            "event": "cross-referenced",
            "source": {"issue": {"number": 2, "pull_request": {}}},
        },
    )

    assert _linked_pull_request_from_events(
        events,
        pull_request_map=pull_request_map,
    ) == (2, "https://github.com/owner/repo/pull/2")
    assert _linked_pull_request_from_events(
        (
            {
                "event": "connected",
                "subject": {
                    "issue": {
                        "number": 3,
                        "html_url": "https://github.com/owner/repo/pull/3",
                        "pull_request": {"number": 3},
                    },
                },
            },
        ),
        pull_request_map={},
    ) == (3, "https://github.com/owner/repo/pull/3")
    assert (
        _linked_pull_request_from_events(
            ({"event": "cross-referenced", "source": {"issue": {}}},),
            pull_request_map={},
        )
        is None
    )


class _WorkspaceService:
    def __init__(self, workspaces: tuple[WorkspaceRecord, ...]) -> None:
        self._workspaces = {
            workspace.workspace_id: workspace for workspace in workspaces
        }
        self.deleted_workspace_ids: list[str] = []

    async def get_workspace_async(self, workspace_id: str) -> WorkspaceRecord:
        return self._workspaces[workspace_id]

    async def fork_workspace_async(
        self,
        source_workspace_id: str,
        *,
        name: str,
        start_ref: str | None = None,
    ) -> WorkspaceRecord:
        _ = start_ref
        source = self._workspaces[source_workspace_id]
        workspace = source.model_copy(update={"workspace_id": name})
        self._workspaces[name] = workspace
        return workspace

    async def delete_workspace_with_options_async(
        self,
        *,
        workspace_id: str,
        remove_directory: bool = False,
    ) -> WorkspaceRecord:
        _ = remove_directory
        self.deleted_workspace_ids.append(workspace_id)
        return self._workspaces.pop(workspace_id)


class _GitHubTriggerService:
    def __init__(
        self,
        *,
        accounts: tuple[GitHubTriggerAccountRecord, ...] | None = None,
        tokens: dict[str, str] | None = None,
    ) -> None:
        self._accounts = (
            (
                GitHubTriggerAccountRecord(
                    account_id="gh_1",
                    name="default",
                    display_name="Default",
                    status=GitHubTriggerAccountStatus.ENABLED,
                    token_configured=True,
                    created_at=_now(),
                    updated_at=_now(),
                ),
            )
            if accounts is None
            else accounts
        )
        self._tokens = {"gh_1": "token"} if tokens is None else tokens

    async def list_accounts_async(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        return self._accounts

    async def resolve_account_token_async(self, account_id: str) -> str | None:
        return self._tokens.get(account_id)


class _GitHubClient:
    def __init__(
        self,
        issues: dict[
            str,
            tuple[
                tuple[str, int] | tuple[str, int, str] | tuple[str, int, str, str],
                ...,
            ],
        ]
        | None = None,
        *,
        pull_requests: dict[str, tuple[tuple[str, int, str | None], ...]] | None = None,
        timelines: dict[str, tuple[int | tuple[int, str], ...]] | None = None,
        github_error: GitHubApiError | None = None,
    ) -> None:
        self._issues = issues or {}
        self._pull_requests = pull_requests or {}
        self._timelines = timelines or {}
        self._github_error = github_error
        self.tokens: list[str] = []
        self.issue_since: list[datetime | None] = []
        self.issue_states: list[str] = []
        self.pull_since: list[datetime | None] = []
        self.pull_request_numbers: list[int] = []

    def set_issues(
        self,
        issues: dict[
            str,
            tuple[
                tuple[str, int] | tuple[str, int, str] | tuple[str, int, str, str],
                ...,
            ],
        ],
    ) -> None:
        self._issues = issues

    async def list_repository_issues(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        state: str = "all",
        updated_since: datetime | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        self.tokens.append(token)
        self.issue_since.append(updated_since)
        self.issue_states.append(state)
        if self._github_error is not None:
            raise self._github_error
        issues: list[dict[str, JsonValue]] = []
        for raw_issue in self._issues.get(f"{owner}/{repo}", ()):
            title = raw_issue[0]
            number = raw_issue[1]
            issue_state = raw_issue[2] if len(raw_issue) > 2 else "open"
            updated_at = raw_issue[3] if len(raw_issue) > 3 else "2026-05-10T08:07:00Z"
            if state != "all" and issue_state != state:
                continue
            issues.append(
                {
                    "number": number,
                    "title": title,
                    "body": "",
                    "html_url": f"https://github.com/{owner}/{repo}/issues/{number}",
                    "state": issue_state,
                    "updated_at": updated_at,
                }
            )
        return tuple(issues)

    async def list_repository_pull_requests(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        state: str = "all",
        updated_since: datetime | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        self.tokens.append(token)
        self.pull_since.append(updated_since)
        pull_requests = (
            {
                "number": number,
                "title": title,
                "body": "",
                "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
                "merged_at": merged_at,
                "merged": merged_at is not None,
                "updated_at": merged_at or "2026-05-10T09:00:00Z",
            }
            for title, number, merged_at in self._pull_requests.get(
                f"{owner}/{repo}", ()
            )
        )
        return tuple(
            pull_request
            for pull_request in pull_requests
            if updated_since is None
            or _json_datetime(pull_request.get("updated_at")) > updated_since
        )

    async def get_repository_pull_request(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        pull_request_number: int,
    ) -> dict[str, JsonValue]:
        self.tokens.append(token)
        self.pull_request_numbers.append(pull_request_number)
        for title, number, merged_at in self._pull_requests.get(f"{owner}/{repo}", ()):
            if number != pull_request_number:
                continue
            return {
                "number": number,
                "title": title,
                "body": "",
                "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
                "merged_at": merged_at,
                "merged": merged_at is not None,
                "updated_at": merged_at or "2026-05-10T09:00:00Z",
            }
        raise GitHubApiError(message="Pull request not found", status_code=404)

    async def list_issue_timeline_events(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> tuple[dict[str, JsonValue], ...]:
        self.tokens.append(token)
        return tuple(
            _timeline_event(owner=owner, repo=repo, value=value)
            for value in self._timelines.get(f"{owner}/{repo}#{issue_number}", ())
        )


def _timeline_event(
    *,
    owner: str,
    repo: str,
    value: int | tuple[int, str],
) -> dict[str, JsonValue]:
    if isinstance(value, tuple):
        pull_request_number = value[0]
        event_name = value[1]
    else:
        pull_request_number = value
        event_name = "cross-referenced"
    return {
        "event": event_name,
        "source": {
            "issue": {
                "number": pull_request_number,
                "html_url": (
                    f"https://github.com/{owner}/{repo}/pull/{pull_request_number}"
                ),
                "pull_request": {
                    "html_url": (
                        f"https://github.com/{owner}/{repo}/pull/{pull_request_number}"
                    )
                },
            }
        },
    }


class _SessionService:
    def __init__(self) -> None:
        self.count = 0
        self.workspace_ids: list[str] = []
        self.session_modes: list[SessionMode | None] = []
        self.normal_root_role_ids: list[str | None] = []
        self.orchestration_preset_ids: list[str | None] = []
        self.sessions: dict[str, SessionRecord] = {}
        self.deleted_session_ids: list[str] = []

    async def create_session_async(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        self.count += 1
        self.workspace_ids.append(workspace_id)
        self.session_modes.append(session_mode)
        self.normal_root_role_ids.append(normal_root_role_id)
        self.orchestration_preset_ids.append(orchestration_preset_id)
        session = SessionRecord(
            session_id=session_id or f"session-{self.count}",
            workspace_id=workspace_id,
            metadata=metadata or {},
            session_mode=session_mode or SessionMode.NORMAL,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.sessions[session.session_id] = session
        return session

    async def get_session_async(self, session_id: str) -> SessionRecord:
        return self.sessions[session_id]

    async def delete_session_async(
        self,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        _ = force
        _ = cascade
        self.deleted_session_ids.append(session_id)
        self.sessions.pop(session_id, None)


class _BlockingSessionService:
    def __init__(self) -> None:
        self.count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.sessions: dict[str, SessionRecord] = {}
        self.deleted_session_ids: list[str] = []

    async def create_session_async(
        self,
        *,
        session_id: str | None = None,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode | None = None,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        self.count += 1
        self.entered.set()
        await self.release.wait()
        session = SessionRecord(
            session_id=session_id or f"session-{self.count}",
            workspace_id=workspace_id,
            metadata=metadata or {},
            session_mode=session_mode or SessionMode.NORMAL,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.sessions[session.session_id] = session
        return session

    async def get_session_async(self, session_id: str) -> SessionRecord:
        return self.sessions[session_id]

    async def delete_session_async(
        self,
        session_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        _ = force
        _ = cascade
        self.deleted_session_ids.append(session_id)
        self.sessions.pop(session_id, None)


class _RunService:
    def __init__(self, runtime: "_RunRuntimeRepository") -> None:
        self._runtime = runtime
        self.count = 0
        self.prompts: list[str] = []
        self.intents: list[IntentInput] = []

    async def create_run_async(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        self.count += 1
        run_id = f"run-{self.count}"
        self.intents.append(intent)
        self.prompts.append(content_parts_to_text(intent.input))
        self._runtime.records[run_id] = RunRuntimeRecord(
            run_id=run_id,
            session_id=intent.session_id,
            status=RunRuntimeStatus.RUNNING,
            created_at=_now(),
            updated_at=_now(),
        )
        return run_id, intent.session_id

    async def ensure_run_started_async(self, run_id: str) -> None:
        self._runtime.set_status(run_id, RunRuntimeStatus.RUNNING)

    async def stop_run_async(self, run_id: str) -> None:
        self._runtime.set_status(run_id, RunRuntimeStatus.STOPPED)


class _FailingRunService:
    def __init__(self) -> None:
        self.count = 0

    async def create_run_async(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        self.count += 1
        raise RuntimeError("run creation failed")

    async def ensure_run_started_async(self, run_id: str) -> None:
        raise AssertionError("run should not be started")

    async def stop_run_async(self, run_id: str) -> None:
        _ = run_id


class _EnsureFailingRunService:
    def __init__(self, runtime: "_RunRuntimeRepository") -> None:
        self._runtime = runtime
        self.count = 0

    async def create_run_async(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        self.count += 1
        run_id = f"run-{self.count}"
        self._runtime.records[run_id] = RunRuntimeRecord(
            run_id=run_id,
            session_id=intent.session_id,
            status=RunRuntimeStatus.RUNNING,
            created_at=_now(),
            updated_at=_now(),
        )
        return run_id, intent.session_id

    async def ensure_run_started_async(self, run_id: str) -> None:
        _ = run_id
        raise RuntimeError("run start failed")

    async def stop_run_async(self, run_id: str) -> None:
        self._runtime.set_status(run_id, RunRuntimeStatus.STOPPED)


class _BlockingEnsureRunService:
    def __init__(self, runtime: "_RunRuntimeRepository") -> None:
        self._runtime = runtime
        self.count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create_run_async(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        self.count += 1
        run_id = f"run-{self.count}"
        self._runtime.records[run_id] = RunRuntimeRecord(
            run_id=run_id,
            session_id=intent.session_id,
            status=RunRuntimeStatus.RUNNING,
            created_at=_now(),
            updated_at=_now(),
        )
        return run_id, intent.session_id

    async def ensure_run_started_async(self, run_id: str) -> None:
        _ = run_id
        self.entered.set()
        await self.release.wait()

    async def stop_run_async(self, run_id: str) -> None:
        self._runtime.set_status(run_id, RunRuntimeStatus.STOPPED)


class _ControlledRunService:
    def __init__(
        self,
        runtime: "_RunRuntimeRepository",
        *,
        block_on_count: int | None = None,
        fail_on_count: int | None = None,
    ) -> None:
        self._runtime = runtime
        self._block_on_count = block_on_count
        self._fail_on_count = fail_on_count
        self.count = 0
        self.prompts: list[str] = []
        self.intents: list[IntentInput] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create_run_async(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        self.count += 1
        if self._fail_on_count == self.count:
            raise RuntimeError("run creation failed")
        if self._block_on_count == self.count:
            self.entered.set()
            await self.release.wait()
        run_id = f"run-{self.count}"
        self.prompts.append(content_parts_to_text(intent.input))
        self.intents.append(intent)
        self._runtime.records[run_id] = RunRuntimeRecord(
            run_id=run_id,
            session_id=intent.session_id,
            status=RunRuntimeStatus.RUNNING,
            created_at=_now(),
            updated_at=_now(),
        )
        return run_id, intent.session_id

    async def ensure_run_started_async(self, run_id: str) -> None:
        self._runtime.set_status(run_id, RunRuntimeStatus.RUNNING)

    async def stop_run_async(self, run_id: str) -> None:
        self._runtime.set_status(run_id, RunRuntimeStatus.STOPPED)


class _RunRuntimeRepository:
    def __init__(self) -> None:
        self.records: dict[str, RunRuntimeRecord] = {}

    async def get_async(self, run_id: str) -> RunRuntimeRecord | None:
        return self.records.get(run_id)

    def set_status(self, run_id: str, status: RunRuntimeStatus) -> None:
        record = self.records[run_id]
        self.records[run_id] = record.model_copy(
            update={"status": status, "updated_at": _now()}
        )


def _service(
    tmp_path: Path,
    *,
    workspaces: tuple[WorkspaceRecord, ...],
    repository: BoardTodoRepository | None = None,
    github: _GitHubClient | None = None,
    github_accounts: tuple[GitHubTriggerAccountRecord, ...] | None = None,
    github_tokens: dict[str, str] | None = None,
    shared_github_token: str | None = None,
    workspace_service: WorkspaceServiceLike | None = None,
    session_service: SessionServiceLike | None = None,
    run_service: SessionRunServiceLike | None = None,
    run_runtime: _RunRuntimeRepository | None = None,
    shell_safety_policy_enabled: bool = True,
) -> _BoardTodoServiceHarness:
    runtime = run_runtime or _RunRuntimeRepository()
    return _BoardTodoServiceHarness(
        repository=repository or BoardTodoRepository(tmp_path / "board-todos.sqlite"),
        workspace_service=workspace_service or _WorkspaceService(workspaces),
        github_trigger_service=_GitHubTriggerService(
            accounts=github_accounts,
            tokens=github_tokens,
        ),
        github_client=github or _GitHubClient(),
        session_service=session_service or _SessionService(),
        run_service=run_service or _RunService(runtime),
        run_runtime_repo=runtime,
        get_shared_github_token=lambda: shared_github_token,
        get_shell_safety_policy_enabled=lambda: shell_safety_policy_enabled,
    )


_TEST_TODO_ISSUE_NUMBER = 9000


def _next_test_issue_number() -> int:
    global _TEST_TODO_ISSUE_NUMBER
    _TEST_TODO_ISSUE_NUMBER += 1
    return _TEST_TODO_ISSUE_NUMBER


async def _create_test_todo(
    service: _BoardTodoServiceHarness,
    *,
    workspace_id: str,
    title: str,
    body: str = "",
    repository_full_name: str = "owner/repo",
    status: BoardTodoStatus = BoardTodoStatus.TODO,
) -> BoardTodoItem:
    issue_number = _next_test_issue_number()
    now = _now()
    return await service.repository_for_tests.create_async(
        BoardTodoItem(
            todo_id=f"todo_test_{issue_number}",
            workspace_id=workspace_id,
            status=status,
            title=title,
            body=body,
            source_provider=BoardTodoSourceProvider.GITHUB,
            source_type=BoardTodoSourceType.GITHUB_ISSUE,
            source_key=f"github:{repository_full_name}:issue:{issue_number}",
            repository_full_name=repository_full_name,
            issue_number=issue_number,
            html_url=f"https://github.com/{repository_full_name}/issues/{issue_number}",
            created_at=now,
            updated_at=now,
        )
    )


async def _preview_request_changes_prompt(
    service: _BoardTodoServiceHarness,
    *,
    todo_id: str,
    feedback: str = "Please revise",
) -> str:
    preview = await service.preview_request_changes_todo(
        todo_id=todo_id,
        payload=BoardTodoPreviewRequestChangesRequest(feedback=feedback),
    )
    return preview.prompt


def _workspace(
    path: Path,
    full_name: str,
    *,
    remote_name: str = "origin",
) -> WorkspaceRecord:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "-C", str(path), "init"), check=True, capture_output=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(path),
            "remote",
            "add",
            remote_name,
            f"git@github.com:{full_name}.git",
        ),
        check=True,
        capture_output=True,
    )
    return WorkspaceRecord(
        workspace_id=path.name,
        default_mount_name="default",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=path,
            ),
        ),
        created_at=_now(),
        updated_at=_now(),
    )


def _workspace_without_remote(path: Path) -> WorkspaceRecord:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "-C", str(path), "init"), check=True, capture_output=True)
    return WorkspaceRecord(
        workspace_id=path.name,
        default_mount_name="default",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=path,
            ),
        ),
        created_at=_now(),
        updated_at=_now(),
    )


def _now() -> datetime:
    return datetime(2026, 5, 10, 9, 0, tzinfo=UTC)


def _json_datetime(value: JsonValue) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return datetime.min.replace(tzinfo=UTC)
