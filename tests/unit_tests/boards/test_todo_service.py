from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable
from datetime import UTC, datetime
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
    BoardTodoItem,
    BoardTodoLinkPullRequestRequest,
    BoardTodoMarkDoneRequest,
    BoardTodoPreviewStartRequest,
    BoardTodoSource,
    BoardTodoSourceCreateRequest,
    BoardTodoSourceKind,
    BoardTodoSourceProvider,
    BoardTodoSourceType,
    BoardTodoSourceUpdateRequest,
    BoardTodoSyncStatus,
    BoardTodoStartRequest,
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
    _json_datetime_or_none,
    _json_int,
    _linked_pull_request_from_events,
    _parse_github_pull_request_url,
    _parse_github_remote,
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


class _GetWorkspaceStub(Protocol):
    def __call__(
        self, self_obj: object, workspace_id: str
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
async def test_start_request_without_mode_uses_session_default(
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

    assert session_service.session_modes == [None]
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

    await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(feedback="Please revise"),
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
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(feedback="Please revise"),
    )

    assert started.status == BoardTodoStatus.IN_PROGRESS
    assert started.session_id is not None
    assert reviewed.status == BoardTodoStatus.REVIEW
    assert changed.status == BoardTodoStatus.IN_PROGRESS
    assert changed.run_id != started.run_id
    assert run_service.prompts[-1].endswith("Please revise")


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
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(feedback="Please revise"),
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
async def test_start_restores_todo_when_run_creation_fails(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo", "owner/repo")
    run_runtime = _RunRuntimeRepository()
    run_service = _FailingRunService()
    service = _service(
        tmp_path,
        workspaces=(workspace,),
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
    changed = await service.request_changes(
        todo_id=item.todo_id,
        payload=BoardTodoStatusUpdateRequest(feedback="Please revise"),
    )

    with pytest.raises(ValueError, match="only review board items can request changes"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(feedback="Please revise again"),
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

    first_change = asyncio.create_task(
        service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(feedback="Please revise"),
        )
    )
    await run_service.entered.wait()

    with pytest.raises(ValueError, match="only review board items can request changes"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(feedback="Please revise again"),
        )

    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 2
    assert board.items[0].status == BoardTodoStatus.IN_PROGRESS
    assert board.items[0].session_id == started.session_id
    assert board.items[0].run_id is None
    assert board.items[0].last_status_reason == "Requesting changes"

    run_service.release.set()
    changed = await first_change

    assert run_service.count == 2
    assert changed.status == BoardTodoStatus.IN_PROGRESS
    assert changed.session_id == started.session_id
    assert changed.run_id == "run-2"
    assert run_service.prompts[-1].endswith("Please revise")


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

    with pytest.raises(RuntimeError, match="run creation failed"):
        await service.request_changes(
            todo_id=item.todo_id,
            payload=BoardTodoStatusUpdateRequest(feedback="Please revise"),
        )

    board = await service.list_board(workspace_id="repo")
    assert run_service.count == 2
    assert board.items[0].status == BoardTodoStatus.REVIEW
    assert board.items[0].session_id == started.session_id
    assert board.items[0].run_id == started.run_id
    assert board.items[0].last_status_reason == "Request changes failed"


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

    async def get_workspace_async(self, workspace_id: str) -> WorkspaceRecord:
        return self._workspaces[workspace_id]


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


class _BlockingSessionService:
    def __init__(self) -> None:
        self.count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.sessions: dict[str, SessionRecord] = {}

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
    session_service: SessionServiceLike | None = None,
    run_service: SessionRunServiceLike | None = None,
    run_runtime: _RunRuntimeRepository | None = None,
    shell_safety_policy_enabled: bool = True,
) -> _BoardTodoServiceHarness:
    runtime = run_runtime or _RunRuntimeRepository()
    return _BoardTodoServiceHarness(
        repository=repository or BoardTodoRepository(tmp_path / "board-todos.sqlite"),
        workspace_service=_WorkspaceService(workspaces),
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
