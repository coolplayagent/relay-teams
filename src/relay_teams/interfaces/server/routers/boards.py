# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, JsonValue

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
    BoardTodoSourceSettingsResponse,
    BoardTodoSourceUpdateRequest,
    BoardTodoStartRequest,
    BoardTodoStatusUpdateRequest,
    BoardTodoSyncChangesRequest,
    BoardTodoSyncRequest,
)
from relay_teams.boards.adapter import (
    BoardTaskState,
    TaskBoardConfig,
    TaskBoardStateMap,
)
from relay_teams.interfaces.server.deps import get_board_todo_service
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

router = APIRouter(prefix="/boards", tags=["Boards"])


class BoardSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_id: str
    adapter: str
    config: dict[str, JsonValue]


class BoardTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_task_id: str
    title: str
    description: str
    state: str
    assignee: str | None = None
    labels: tuple[str, ...] = ()
    source_url: str = ""


class UpdateTaskStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1)


class StateMapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_status_to_board: dict[str, str]
    board_state_to_task_status: dict[str, tuple[str, ...]]


def _config_to_summary(cfg: TaskBoardConfig) -> BoardSummaryResponse:
    return BoardSummaryResponse(
        board_id=cfg.board_id,
        adapter=cfg.adapter,
        config=cfg.model_dump(mode="json"),
    )


# GET /api/boards
def list_boards() -> list[BoardSummaryResponse]:
    """List configured boards."""
    return []


# GET /api/boards/todos
async def list_board_todos(
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
    workspace_id: str = Query(min_length=1),
    include_archived: bool = False,
) -> BoardTodoBoardResponse:
    try:
        return await service.list_board(
            workspace_id=workspace_id,
            include_archived=include_archived,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# GET /api/boards/todos:changes
async def list_board_todo_changes(
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
    workspace_id: str = Query(min_length=1),
    include_archived: bool = False,
    after_revision: int = Query(default=0, ge=0),
) -> BoardTodoDeltaResponse:
    try:
        return await service.list_board_changes(
            workspace_id=workspace_id,
            include_archived=include_archived,
            after_revision=after_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# POST /api/boards/todos:sync
async def sync_board_todos(
    request: BoardTodoSyncRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoBoardResponse:
    try:
        return await service.sync_board(
            workspace_id=request.workspace_id,
            include_archived=request.include_archived,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# POST /api/boards/todos:sync-changes
async def sync_board_todo_changes(
    request: BoardTodoSyncChangesRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoDeltaResponse:
    try:
        return await service.sync_board_changes(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# GET /api/boards/todo-sources
async def list_board_todo_sources(
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
    workspace_id: str = Query(min_length=1),
) -> BoardTodoSourceSettingsResponse:
    try:
        return await service.list_sources(workspace_id=workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# POST /api/boards/todo-sources
async def create_board_todo_source(
    request: BoardTodoSourceCreateRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoSource:
    try:
        return await service.create_source(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# PATCH /api/boards/todo-sources/{source_id}
async def update_board_todo_source(
    source_id: str,
    request: BoardTodoSourceUpdateRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoSource:
    try:
        return await service.update_source(source_id=source_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# DELETE /api/boards/todo-sources/{source_id}
async def delete_board_todo_source(
    source_id: str,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoSourceDeleteResponse:
    try:
        return await service.delete_source(source_id=source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:preview-start
async def preview_start_board_todo(
    todo_id: str,
    request: BoardTodoPreviewStartRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoPreviewStartResponse:
    try:
        return await service.preview_start_todo(todo_id=todo_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:start
async def start_board_todo(
    todo_id: str,
    request: BoardTodoStartRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoItem:
    try:
        return await service.start_todo(todo_id=todo_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:request-changes
async def request_board_todo_changes(
    todo_id: str,
    request: BoardTodoStatusUpdateRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoItem:
    try:
        return await service.request_changes(todo_id=todo_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:mark-done
async def mark_board_todo_done(
    todo_id: str,
    request: BoardTodoMarkDoneRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoItem:
    try:
        return await service.mark_done(todo_id=todo_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:archive
async def archive_board_todo(
    todo_id: str,
    request: BoardTodoArchiveRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoItem:
    try:
        return await service.archive_todo(todo_id=todo_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:restore
async def restore_board_todo(
    todo_id: str,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoItem:
    try:
        return await service.restore_todo(todo_id=todo_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# POST /api/boards/todos/{todo_id}:link-pr
async def link_board_todo_pull_request(
    todo_id: str,
    request: BoardTodoLinkPullRequestRequest,
    service: Annotated[BoardTodoService, Depends(get_board_todo_service)],
) -> BoardTodoItem:
    try:
        return await service.link_pull_request(todo_id=todo_id, payload=request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# GET /api/boards/{board_id}/tasks
async def list_board_tasks(board_id: str) -> list[BoardTaskResponse]:
    """List tasks on a board."""
    LOGGER.info("listing tasks for board %s", board_id)
    return []


# POST /api/boards/{board_id}/sync
async def sync_board(board_id: str) -> dict[str, JsonValue]:
    """Manually trigger a board sync."""
    LOGGER.info("board sync requested for %s", board_id)
    return {"synced": True, "board_id": board_id}


# PUT /api/boards/{board_id}/tasks/{task_id}/state
async def update_board_task_state(
    board_id: str,
    task_id: str,
    request: UpdateTaskStateRequest,
) -> dict[str, JsonValue]:
    """Manually update a board task's state."""
    try:
        BoardTaskState(request.state)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state: {request.state}",
        ) from None
    return {
        "updated": True,
        "board_id": board_id,
        "task_id": task_id,
        "state": request.state,
    }


# GET /api/boards/state-map
def get_state_map() -> StateMapResponse:
    """Get the current TaskBoardStateMap configuration."""
    sm = TaskBoardStateMap()
    return StateMapResponse(
        task_status_to_board={
            k.value: v.value for k, v in sm.task_status_to_board.items()
        },
        board_state_to_task_status={
            k.value: tuple(s.value for s in v)
            for k, v in sm.board_state_to_task_status.items()
        },
    )


router.add_api_route("", list_boards, methods=["GET"])
router.add_api_route(
    "/todos",
    list_board_todos,
    methods=["GET"],
    response_model=BoardTodoBoardResponse,
)
router.add_api_route(
    "/todos:changes",
    list_board_todo_changes,
    methods=["GET"],
    response_model=BoardTodoDeltaResponse,
)
router.add_api_route(
    "/todos:sync",
    sync_board_todos,
    methods=["POST"],
    response_model=BoardTodoBoardResponse,
)
router.add_api_route(
    "/todos:sync-changes",
    sync_board_todo_changes,
    methods=["POST"],
    response_model=BoardTodoDeltaResponse,
)
router.add_api_route(
    "/todo-sources",
    list_board_todo_sources,
    methods=["GET"],
    response_model=BoardTodoSourceSettingsResponse,
)
router.add_api_route(
    "/todo-sources",
    create_board_todo_source,
    methods=["POST"],
    response_model=BoardTodoSource,
)
router.add_api_route(
    "/todo-sources/{source_id}",
    update_board_todo_source,
    methods=["PATCH"],
    response_model=BoardTodoSource,
)
router.add_api_route(
    "/todo-sources/{source_id}",
    delete_board_todo_source,
    methods=["DELETE"],
    response_model=BoardTodoSourceDeleteResponse,
)
router.add_api_route(
    "/todos/{todo_id}:preview-start",
    preview_start_board_todo,
    methods=["POST"],
    response_model=BoardTodoPreviewStartResponse,
)
router.add_api_route(
    "/todos/{todo_id}:start",
    start_board_todo,
    methods=["POST"],
    response_model=BoardTodoItem,
)
router.add_api_route(
    "/todos/{todo_id}:request-changes",
    request_board_todo_changes,
    methods=["POST"],
    response_model=BoardTodoItem,
)
router.add_api_route(
    "/todos/{todo_id}:mark-done",
    mark_board_todo_done,
    methods=["POST"],
    response_model=BoardTodoItem,
)
router.add_api_route(
    "/todos/{todo_id}:archive",
    archive_board_todo,
    methods=["POST"],
    response_model=BoardTodoItem,
)
router.add_api_route(
    "/todos/{todo_id}:restore",
    restore_board_todo,
    methods=["POST"],
    response_model=BoardTodoItem,
)
router.add_api_route(
    "/todos/{todo_id}:link-pr",
    link_board_todo_pull_request,
    methods=["POST"],
    response_model=BoardTodoItem,
)
router.add_api_route("/{board_id}/tasks", list_board_tasks, methods=["GET"])
router.add_api_route("/{board_id}/sync", sync_board, methods=["POST"])
router.add_api_route(
    "/{board_id}/tasks/{task_id}/state",
    update_board_task_state,
    methods=["PUT"],
)
router.add_api_route("/state-map", get_state_map, methods=["GET"])
