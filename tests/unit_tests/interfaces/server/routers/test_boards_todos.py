from __future__ import annotations

import importlib.util
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from relay_teams.boards import (
    BoardTodoArchiveRequest,
    BoardTodoBoardResponse,
    BoardTodoDeltaResponse,
    BoardTodoItem,
    BoardTodoLinkPullRequestRequest,
    BoardTodoMarkDoneRequest,
    BoardTodoPreviewStartRequest,
    BoardTodoPreviewStartResponse,
    BoardTodoService,
    BoardTodoSource,
    BoardTodoSourceCreateRequest,
    BoardTodoSourceDeleteResponse,
    BoardTodoSourceKind,
    BoardTodoStartRequest,
    BoardTodoSourceSettingsResponse,
    BoardTodoSourceUpdateRequest,
    BoardTodoStatusUpdateRequest,
    BoardTodoSyncChangesRequest,
    BoardTodoSyncRequest,
)
from relay_teams.boards.todo_models import (
    BoardTodoSourceProvider,
    BoardTodoSourceType,
    BoardTodoStatus,
)


def _load_boards_router_module() -> types.ModuleType:
    module_name = "_board_todo_router_under_test"
    deps_name = "relay_teams.interfaces.server.deps"
    repo_root = Path(__file__).resolve().parents[5]
    source_path = (
        repo_root
        / "src"
        / "relay_teams"
        / "interfaces"
        / "server"
        / "routers"
        / "boards.py"
    )
    deps_stub = types.ModuleType(deps_name)

    def get_board_todo_service(_request: object) -> object:
        return object()

    setattr(deps_stub, "get_board_todo_service", get_board_todo_service)
    previous_deps = sys.modules.get(deps_name)
    sys.modules[deps_name] = deps_stub
    try:
        spec = importlib.util.spec_from_file_location(module_name, source_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load boards router module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_deps is None:
            sys.modules.pop(deps_name, None)
        else:
            sys.modules[deps_name] = previous_deps


_BOARDS_ROUTER = _load_boards_router_module()
archive_board_todo = _BOARDS_ROUTER.archive_board_todo
create_board_todo_source = _BOARDS_ROUTER.create_board_todo_source
delete_board_todo_source = _BOARDS_ROUTER.delete_board_todo_source
link_board_todo_pull_request = _BOARDS_ROUTER.link_board_todo_pull_request
mark_board_todo_done = _BOARDS_ROUTER.mark_board_todo_done
list_board_todo_changes = _BOARDS_ROUTER.list_board_todo_changes
list_board_todo_sources = _BOARDS_ROUTER.list_board_todo_sources
list_board_todos = _BOARDS_ROUTER.list_board_todos
preview_start_board_todo = _BOARDS_ROUTER.preview_start_board_todo
request_board_todo_changes = _BOARDS_ROUTER.request_board_todo_changes
restore_board_todo = _BOARDS_ROUTER.restore_board_todo
start_board_todo = _BOARDS_ROUTER.start_board_todo
sync_board_todo_changes = _BOARDS_ROUTER.sync_board_todo_changes
sync_board_todos = _BOARDS_ROUTER.sync_board_todos
update_board_todo_source = _BOARDS_ROUTER.update_board_todo_source


@pytest.mark.asyncio
async def test_board_todo_router_delegates_to_service() -> None:
    service = _BoardTodoRouteService()
    route_service = cast(BoardTodoService, service)

    listed = await list_board_todos(
        service=route_service,
        workspace_id="workspace",
        include_archived=True,
    )
    changes = await list_board_todo_changes(
        service=route_service,
        workspace_id="workspace",
        include_archived=False,
        after_revision=3,
    )
    synced = await sync_board_todos(
        request=BoardTodoSyncRequest(workspace_id="workspace", include_archived=True),
        service=route_service,
    )
    synced_changes = await sync_board_todo_changes(
        request=BoardTodoSyncChangesRequest(
            workspace_id="workspace",
            include_archived=True,
            after_revision=4,
            force_full=True,
        ),
        service=route_service,
    )
    sources = await list_board_todo_sources(
        service=route_service,
        workspace_id="workspace",
    )
    created_source = await create_board_todo_source(
        request=BoardTodoSourceCreateRequest(
            workspace_id="workspace",
            display_name="Configured",
            repository_full_name="owner/configured",
        ),
        service=route_service,
    )
    updated_source = await update_board_todo_source(
        source_id="bsrc_github",
        request=BoardTodoSourceUpdateRequest(
            workspace_id="workspace",
            repository_full_name="owner/updated",
        ),
        service=route_service,
    )
    deleted_source = await delete_board_todo_source(
        source_id="bsrc_github",
        service=route_service,
    )
    preview = await preview_start_board_todo(
        todo_id="todo_1",
        request=BoardTodoPreviewStartRequest(view_workspace_id="workspace"),
        service=route_service,
    )
    started = await start_board_todo(
        todo_id="todo_1",
        request=BoardTodoStartRequest(final_prompt="Process", yolo=False),
        service=route_service,
    )
    requested = await request_board_todo_changes(
        todo_id="todo_1",
        request=BoardTodoStatusUpdateRequest(feedback="Revise", yolo=True),
        service=route_service,
    )
    marked_done = await mark_board_todo_done(
        todo_id="todo_1",
        request=BoardTodoMarkDoneRequest(reason="Looks good"),
        service=route_service,
    )
    archived = await archive_board_todo(
        todo_id="todo_1",
        request=BoardTodoArchiveRequest(reason="Done elsewhere"),
        service=route_service,
    )
    restored = await restore_board_todo(todo_id="todo_1", service=route_service)
    linked = await link_board_todo_pull_request(
        todo_id="todo_1",
        request=BoardTodoLinkPullRequestRequest(
            pull_request_number=12,
            pull_request_url="https://github.com/owner/repo/pull/12",
        ),
        service=route_service,
    )

    assert listed.workspace_id == "workspace"
    assert changes.revision == 7
    assert synced.synced_at == _NOW
    assert synced_changes.changed_items == (_item(),)
    assert sources.board_workspace_id == "workspace"
    assert created_source.repository_full_name == "owner/configured"
    assert updated_source.repository_full_name == "owner/updated"
    assert deleted_source.deleted is True
    assert preview.prompt == "Preview prompt"
    assert started.status == BoardTodoStatus.IN_PROGRESS
    assert requested.last_status_reason == "Changes requested by user"
    assert marked_done.status == BoardTodoStatus.DONE
    assert archived.status == BoardTodoStatus.ARCHIVED
    assert restored.status == BoardTodoStatus.TODO
    assert linked.linked_pr_number == 12
    assert service.calls == [
        "list_board:workspace:True",
        "list_board_changes:workspace:False:3",
        "sync_board:workspace:True",
        "sync_board_changes:workspace:True:4:True",
        "list_sources:workspace",
        "create_source:workspace:owner/configured",
        "update_source:bsrc_github:owner/updated",
        "delete_source:bsrc_github",
        "preview_start_todo:todo_1:workspace",
        "start_todo:todo_1:Process:False",
        "request_changes:todo_1:Revise:True",
        "mark_done:todo_1:Looks good",
        "archive_todo:todo_1:Done elsewhere",
        "restore_todo:todo_1",
        "link_pull_request:todo_1:12",
    ]


def test_board_todo_router_does_not_register_manual_create_route() -> None:
    post_todo_routes = [
        route
        for route in _BOARDS_ROUTER.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/boards/todos"
        and "POST" in route.methods
    ]

    assert post_todo_routes == []


def test_board_todo_router_registers_mark_done_route() -> None:
    mark_done_routes = [
        route
        for route in _BOARDS_ROUTER.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/boards/todos/{todo_id}:mark-done"
        and "POST" in route.methods
    ]

    assert len(mark_done_routes) == 1


@pytest.mark.asyncio
async def test_board_todo_router_maps_service_key_errors() -> None:
    service = _FailingBoardTodoRouteService(KeyError("missing"))
    route_service = cast(BoardTodoService, service)

    with pytest.raises(HTTPException) as list_error:
        await list_board_todos(service=route_service, workspace_id="workspace")
    with pytest.raises(HTTPException) as changes_error:
        await list_board_todo_changes(service=route_service, workspace_id="workspace")
    with pytest.raises(HTTPException) as sync_error:
        await sync_board_todos(
            request=BoardTodoSyncRequest(workspace_id="workspace"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as sync_changes_error:
        await sync_board_todo_changes(
            request=BoardTodoSyncChangesRequest(workspace_id="workspace"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as list_sources_error:
        await list_board_todo_sources(
            service=route_service,
            workspace_id="workspace",
        )
    with pytest.raises(HTTPException) as create_source_error:
        await create_board_todo_source(
            request=BoardTodoSourceCreateRequest(
                workspace_id="workspace",
                display_name="Configured",
                repository_full_name="owner/repo",
            ),
            service=route_service,
        )
    with pytest.raises(HTTPException) as update_source_error:
        await update_board_todo_source(
            source_id="bsrc_github",
            request=BoardTodoSourceUpdateRequest(workspace_id="workspace"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as delete_source_error:
        await delete_board_todo_source(
            source_id="bsrc_github",
            service=route_service,
        )
    with pytest.raises(HTTPException) as preview_error:
        await preview_start_board_todo(
            todo_id="todo_1",
            request=BoardTodoPreviewStartRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as start_error:
        await start_board_todo(
            todo_id="todo_1",
            request=BoardTodoStartRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as request_error:
        await request_board_todo_changes(
            todo_id="todo_1",
            request=BoardTodoStatusUpdateRequest(feedback="Again"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as mark_done_error:
        await mark_board_todo_done(
            todo_id="todo_1",
            request=BoardTodoMarkDoneRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as archive_error:
        await archive_board_todo(
            todo_id="todo_1",
            request=BoardTodoArchiveRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as restore_error:
        await restore_board_todo(todo_id="todo_1", service=route_service)
    with pytest.raises(HTTPException) as link_error:
        await link_board_todo_pull_request(
            todo_id="todo_1",
            request=BoardTodoLinkPullRequestRequest(pull_request_number=12),
            service=route_service,
        )

    assert list_error.value.status_code == 404
    assert changes_error.value.status_code == 404
    assert sync_error.value.status_code == 404
    assert sync_changes_error.value.status_code == 404
    assert list_sources_error.value.status_code == 404
    assert create_source_error.value.status_code == 404
    assert update_source_error.value.status_code == 404
    assert delete_source_error.value.status_code == 404
    assert preview_error.value.status_code == 404
    assert start_error.value.status_code == 404
    assert request_error.value.status_code == 404
    assert mark_done_error.value.status_code == 404
    assert archive_error.value.status_code == 404
    assert restore_error.value.status_code == 404
    assert link_error.value.status_code == 404


@pytest.mark.asyncio
async def test_board_todo_router_maps_service_value_errors() -> None:
    service = _FailingBoardTodoRouteService(ValueError("invalid"))
    route_service = cast(BoardTodoService, service)

    with pytest.raises(HTTPException) as list_error:
        await list_board_todos(service=route_service, workspace_id="workspace")
    with pytest.raises(HTTPException) as changes_error:
        await list_board_todo_changes(service=route_service, workspace_id="workspace")
    with pytest.raises(HTTPException) as sync_error:
        await sync_board_todos(
            request=BoardTodoSyncRequest(workspace_id="workspace"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as sync_changes_error:
        await sync_board_todo_changes(
            request=BoardTodoSyncChangesRequest(workspace_id="workspace"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as list_sources_error:
        await list_board_todo_sources(
            service=route_service,
            workspace_id="workspace",
        )
    with pytest.raises(HTTPException) as create_source_error:
        await create_board_todo_source(
            request=BoardTodoSourceCreateRequest(
                workspace_id="workspace",
                display_name="Configured",
                repository_full_name="owner/repo",
            ),
            service=route_service,
        )
    with pytest.raises(HTTPException) as update_source_error:
        await update_board_todo_source(
            source_id="bsrc_github",
            request=BoardTodoSourceUpdateRequest(workspace_id="workspace"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as delete_source_error:
        await delete_board_todo_source(
            source_id="bsrc_github",
            service=route_service,
        )
    with pytest.raises(HTTPException) as preview_error:
        await preview_start_board_todo(
            todo_id="todo_1",
            request=BoardTodoPreviewStartRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as start_error:
        await start_board_todo(
            todo_id="todo_1",
            request=BoardTodoStartRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as request_error:
        await request_board_todo_changes(
            todo_id="todo_1",
            request=BoardTodoStatusUpdateRequest(feedback="Again"),
            service=route_service,
        )
    with pytest.raises(HTTPException) as mark_done_error:
        await mark_board_todo_done(
            todo_id="todo_1",
            request=BoardTodoMarkDoneRequest(),
            service=route_service,
        )
    with pytest.raises(HTTPException) as restore_error:
        await restore_board_todo(todo_id="todo_1", service=route_service)
    with pytest.raises(HTTPException) as link_error:
        await link_board_todo_pull_request(
            todo_id="todo_1",
            request=BoardTodoLinkPullRequestRequest(pull_request_number=12),
            service=route_service,
        )

    assert list_error.value.status_code == 422
    assert changes_error.value.status_code == 422
    assert sync_error.value.status_code == 422
    assert sync_changes_error.value.status_code == 422
    assert list_sources_error.value.status_code == 422
    assert create_source_error.value.status_code == 422
    assert update_source_error.value.status_code == 422
    assert delete_source_error.value.status_code == 409
    assert preview_error.value.status_code == 409
    assert start_error.value.status_code == 409
    assert request_error.value.status_code == 409
    assert mark_done_error.value.status_code == 409
    assert restore_error.value.status_code == 409
    assert link_error.value.status_code == 409


_NOW = datetime(2026, 5, 10, 12, 30, tzinfo=UTC)


def _item(
    *,
    title: str = "Issue",
    status: BoardTodoStatus = BoardTodoStatus.TODO,
) -> BoardTodoItem:
    return BoardTodoItem(
        todo_id="todo_1",
        workspace_id="workspace",
        status=status,
        title=title,
        source_provider=BoardTodoSourceProvider.GITHUB,
        source_type=BoardTodoSourceType.GITHUB_ISSUE,
        source_key="github:owner/repo:issue:1",
        repository_full_name="owner/repo",
        issue_number=1,
        html_url="https://github.com/owner/repo/issues/1",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _source(
    *,
    repository_full_name: str = "owner/repo",
) -> BoardTodoSource:
    return BoardTodoSource(
        source_id="bsrc_github",
        workspace_id="workspace",
        kind=BoardTodoSourceKind.GITHUB_ISSUES,
        provider=BoardTodoSourceProvider.GITHUB,
        display_name="GitHub",
        enabled=True,
        repository_full_name=repository_full_name,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _BoardTodoRouteService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        self.calls.append(f"list_board:{workspace_id}:{include_archived}")
        return BoardTodoBoardResponse(
            workspace_id=workspace_id,
            repository_full_name="owner/repo",
            items=(_item(),),
            revision=5,
        )

    async def list_board_changes(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
        after_revision: int = 0,
    ) -> BoardTodoDeltaResponse:
        self.calls.append(
            f"list_board_changes:{workspace_id}:{include_archived}:{after_revision}"
        )
        return BoardTodoDeltaResponse(
            workspace_id=workspace_id,
            repository_full_name="owner/repo",
            changed_items=(_item(),),
            revision=7,
        )

    async def sync_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        self.calls.append(f"sync_board:{workspace_id}:{include_archived}")
        return BoardTodoBoardResponse(
            workspace_id=workspace_id,
            repository_full_name="owner/repo",
            items=(_item(),),
            synced_at=_NOW,
            revision=8,
        )

    async def sync_board_changes(
        self,
        request: BoardTodoSyncChangesRequest,
    ) -> BoardTodoDeltaResponse:
        self.calls.append(
            "sync_board_changes:"
            f"{request.workspace_id}:{request.include_archived}:"
            f"{request.after_revision}:{request.force_full}"
        )
        return BoardTodoDeltaResponse(
            workspace_id=request.workspace_id,
            repository_full_name="owner/repo",
            changed_items=(_item(),),
            synced_at=_NOW,
            revision=9,
        )

    async def list_sources(
        self,
        *,
        workspace_id: str,
    ) -> BoardTodoSourceSettingsResponse:
        self.calls.append(f"list_sources:{workspace_id}")
        return BoardTodoSourceSettingsResponse(
            workspace_id=workspace_id,
            board_workspace_id=workspace_id,
            view_workspace_id=workspace_id,
            sources=(),
        )

    async def create_source(
        self,
        payload: BoardTodoSourceCreateRequest,
    ) -> BoardTodoSource:
        self.calls.append(
            f"create_source:{payload.workspace_id}:{payload.repository_full_name}"
        )
        return _source(
            repository_full_name=payload.repository_full_name or "owner/repo"
        )

    async def update_source(
        self,
        *,
        source_id: str,
        payload: BoardTodoSourceUpdateRequest,
    ) -> BoardTodoSource:
        self.calls.append(f"update_source:{source_id}:{payload.repository_full_name}")
        return _source(
            repository_full_name=payload.repository_full_name or "owner/repo"
        )

    async def delete_source(
        self,
        *,
        source_id: str,
    ) -> BoardTodoSourceDeleteResponse:
        self.calls.append(f"delete_source:{source_id}")
        return BoardTodoSourceDeleteResponse(deleted=True, source_id=source_id)

    async def preview_start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoPreviewStartRequest,
    ) -> BoardTodoPreviewStartResponse:
        self.calls.append(f"preview_start_todo:{todo_id}:{payload.view_workspace_id}")
        return BoardTodoPreviewStartResponse(
            todo_id=todo_id,
            board_workspace_id="workspace",
            view_workspace_id=payload.view_workspace_id or "workspace",
            prompt="Preview prompt",
        )

    async def start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStartRequest,
    ) -> BoardTodoItem:
        self.calls.append(f"start_todo:{todo_id}:{payload.final_prompt}:{payload.yolo}")
        return _item(status=BoardTodoStatus.IN_PROGRESS)

    async def request_changes(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStatusUpdateRequest,
    ) -> BoardTodoItem:
        self.calls.append(
            f"request_changes:{todo_id}:{payload.feedback}:{payload.yolo}"
        )
        return _item().model_copy(
            update={"last_status_reason": "Changes requested by user"}
        )

    async def mark_done(
        self,
        *,
        todo_id: str,
        payload: BoardTodoMarkDoneRequest,
    ) -> BoardTodoItem:
        self.calls.append(f"mark_done:{todo_id}:{payload.reason}")
        return _item(status=BoardTodoStatus.DONE).model_copy(
            update={"last_status_reason": payload.reason}
        )

    async def archive_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoArchiveRequest,
    ) -> BoardTodoItem:
        self.calls.append(f"archive_todo:{todo_id}:{payload.reason}")
        return _item(status=BoardTodoStatus.ARCHIVED).model_copy(
            update={"archived_at": _NOW}
        )

    async def restore_todo(self, *, todo_id: str) -> BoardTodoItem:
        self.calls.append(f"restore_todo:{todo_id}")
        return _item()

    async def link_pull_request(
        self,
        *,
        todo_id: str,
        payload: BoardTodoLinkPullRequestRequest,
    ) -> BoardTodoItem:
        self.calls.append(f"link_pull_request:{todo_id}:{payload.pull_request_number}")
        return _item().model_copy(
            update={
                "linked_pr_number": payload.pull_request_number,
                "linked_pr_url": payload.pull_request_url,
            }
        )


class _FailingBoardTodoRouteService:
    def __init__(self, error: KeyError | ValueError) -> None:
        self._error = error

    async def list_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        raise self._error

    async def list_board_changes(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
        after_revision: int = 0,
    ) -> BoardTodoDeltaResponse:
        raise self._error

    async def sync_board(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> BoardTodoBoardResponse:
        raise self._error

    async def sync_board_changes(
        self,
        request: BoardTodoSyncChangesRequest,
    ) -> BoardTodoDeltaResponse:
        raise self._error

    async def list_sources(
        self,
        *,
        workspace_id: str,
    ) -> BoardTodoSourceSettingsResponse:
        raise self._error

    async def create_source(
        self,
        payload: BoardTodoSourceCreateRequest,
    ) -> BoardTodoSource:
        raise self._error

    async def update_source(
        self,
        *,
        source_id: str,
        payload: BoardTodoSourceUpdateRequest,
    ) -> BoardTodoSource:
        raise self._error

    async def delete_source(
        self,
        *,
        source_id: str,
    ) -> BoardTodoSourceDeleteResponse:
        raise self._error

    async def preview_start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoPreviewStartRequest,
    ) -> BoardTodoPreviewStartResponse:
        raise self._error

    async def start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStartRequest,
    ) -> BoardTodoItem:
        raise self._error

    async def request_changes(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStatusUpdateRequest,
    ) -> BoardTodoItem:
        raise self._error

    async def mark_done(
        self,
        *,
        todo_id: str,
        payload: BoardTodoMarkDoneRequest,
    ) -> BoardTodoItem:
        raise self._error

    async def archive_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoArchiveRequest,
    ) -> BoardTodoItem:
        raise self._error

    async def restore_todo(self, *, todo_id: str) -> BoardTodoItem:
        raise self._error

    async def link_pull_request(
        self,
        *,
        todo_id: str,
        payload: BoardTodoLinkPullRequestRequest,
    ) -> BoardTodoItem:
        raise self._error
