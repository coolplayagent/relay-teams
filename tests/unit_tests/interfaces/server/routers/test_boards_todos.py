from __future__ import annotations

import importlib.util
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException

from relay_teams.boards import (
    BoardTodoArchiveRequest,
    BoardTodoBoardResponse,
    BoardTodoCreateInput,
    BoardTodoDeltaResponse,
    BoardTodoItem,
    BoardTodoLinkPullRequestRequest,
    BoardTodoService,
    BoardTodoStartRequest,
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
create_board_todo = _BOARDS_ROUTER.create_board_todo
link_board_todo_pull_request = _BOARDS_ROUTER.link_board_todo_pull_request
list_board_todo_changes = _BOARDS_ROUTER.list_board_todo_changes
list_board_todos = _BOARDS_ROUTER.list_board_todos
request_board_todo_changes = _BOARDS_ROUTER.request_board_todo_changes
restore_board_todo = _BOARDS_ROUTER.restore_board_todo
start_board_todo = _BOARDS_ROUTER.start_board_todo
sync_board_todo_changes = _BOARDS_ROUTER.sync_board_todo_changes
sync_board_todos = _BOARDS_ROUTER.sync_board_todos


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
    created = await create_board_todo(
        request=BoardTodoCreateInput(
            workspace_id="workspace",
            title="Manual",
            body="Body",
        ),
        service=route_service,
    )
    started = await start_board_todo(
        todo_id="todo_1",
        request=BoardTodoStartRequest(prompt="Process", yolo=False),
        service=route_service,
    )
    requested = await request_board_todo_changes(
        todo_id="todo_1",
        request=BoardTodoStatusUpdateRequest(feedback="Revise", yolo=True),
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
    assert created.title == "Manual"
    assert started.status == BoardTodoStatus.IN_PROGRESS
    assert requested.last_status_reason == "Changes requested by user"
    assert archived.status == BoardTodoStatus.ARCHIVED
    assert restored.status == BoardTodoStatus.TODO
    assert linked.linked_pr_number == 12
    assert service.calls == [
        "list_board:workspace:True",
        "list_board_changes:workspace:False:3",
        "sync_board:workspace:True",
        "sync_board_changes:workspace:True:4:True",
        "create_todo:workspace:Manual",
        "start_todo:todo_1:Process:False",
        "request_changes:todo_1:Revise:True",
        "archive_todo:todo_1:Done elsewhere",
        "restore_todo:todo_1",
        "link_pull_request:todo_1:12",
    ]


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
    assert start_error.value.status_code == 404
    assert request_error.value.status_code == 404
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
    with pytest.raises(HTTPException) as create_error:
        await create_board_todo(
            request=BoardTodoCreateInput(workspace_id="workspace", title="Manual"),
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
    assert create_error.value.status_code == 422
    assert start_error.value.status_code == 409
    assert request_error.value.status_code == 409
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

    async def create_todo(self, request: BoardTodoCreateInput) -> BoardTodoItem:
        self.calls.append(f"create_todo:{request.workspace_id}:{request.title}")
        return _item(title=request.title)

    async def start_todo(
        self,
        *,
        todo_id: str,
        payload: BoardTodoStartRequest,
    ) -> BoardTodoItem:
        self.calls.append(f"start_todo:{todo_id}:{payload.prompt}:{payload.yolo}")
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

    async def create_todo(self, request: BoardTodoCreateInput) -> BoardTodoItem:
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
