# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.agents.orchestration.board.adapter import (
    BoardTaskState,
    TaskBoardConfig,
    TaskBoardStateMap,
)
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


# GET /api/boards/{board_id}/tasks
async def list_board_tasks(board_id: str) -> list[BoardTaskResponse]:
    """List tasks on a board."""
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
router.add_api_route("/{board_id}/tasks", list_board_tasks, methods=["GET"])
router.add_api_route("/{board_id}/sync", sync_board, methods=["POST"])
router.add_api_route(
    "/{board_id}/tasks/{task_id}/state",
    update_board_task_state,
    methods=["PUT"],
)
router.add_api_route("/state-map", get_state_map, methods=["GET"])
