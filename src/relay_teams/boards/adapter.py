# -*- coding: utf-8 -*-
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.agents.tasks.enums import TaskStatus


class BoardTaskState(str, Enum):
    """Task board generic state mapped to each tracker's specific state."""

    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BoardEventKind(str, Enum):
    """Board event types."""

    TASK_CREATED = "task_created"
    TASK_MOVED = "task_moved"
    TASK_ASSIGNED = "task_assigned"
    TASK_UPDATED = "task_updated"
    TASK_COMMENTED = "task_commented"


class BoardTask(BaseModel):
    """Unified task board entry model."""

    model_config = ConfigDict(extra="forbid")

    board_task_id: str = Field(min_length=1)
    title: str
    description: str = ""
    state: BoardTaskState
    assignee: str | None = None
    labels: tuple[str, ...] = ()
    source_url: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw_payload: dict[str, JsonValue] = Field(default_factory=dict)


class TaskBoardStateMap(BaseModel):
    """Bidirectional mapping between internal TaskStatus and BoardTaskState."""

    task_status_to_board: dict[TaskStatus, BoardTaskState] = Field(
        default_factory=lambda: {
            TaskStatus.CREATED: BoardTaskState.BACKLOG,
            TaskStatus.ASSIGNED: BoardTaskState.READY,
            TaskStatus.RUNNING: BoardTaskState.IN_PROGRESS,
            TaskStatus.STOPPED: BoardTaskState.BLOCKED,
            TaskStatus.COMPLETED: BoardTaskState.COMPLETED,
            TaskStatus.FAILED: BoardTaskState.CANCELLED,
            TaskStatus.TIMEOUT: BoardTaskState.BLOCKED,
        }
    )
    board_state_to_task_status: dict[BoardTaskState, tuple[TaskStatus, ...]] = Field(
        default_factory=lambda: {
            BoardTaskState.BACKLOG: (TaskStatus.CREATED,),
            BoardTaskState.READY: (TaskStatus.ASSIGNED,),
            BoardTaskState.IN_PROGRESS: (TaskStatus.RUNNING,),
            BoardTaskState.BLOCKED: (TaskStatus.STOPPED, TaskStatus.TIMEOUT),
            BoardTaskState.IN_REVIEW: (TaskStatus.RUNNING,),
            BoardTaskState.COMPLETED: (TaskStatus.COMPLETED,),
            BoardTaskState.CANCELLED: (TaskStatus.FAILED,),
        }
    )


class TaskBoardConfig(BaseModel):
    """Board integration configuration."""

    model_config = ConfigDict(extra="forbid")

    board_id: str = Field(min_length=1)
    adapter: str  # "internal", "github", "linear"
    # GitHub configuration
    github_repo: str = ""
    github_token_env: str = ""
    # Linear configuration
    linear_api_key_env: str = ""
    linear_team_id: str = ""
    # General configuration
    poll_interval_seconds: int = Field(default=30, ge=5)
    stall_timeout_seconds: int = Field(default=600, ge=60)
    auto_claim: bool = False
    auto_comment: bool = False


class TaskBoardAdapter(ABC):
    """Abstract base class -- all external tracker adapters must implement this."""

    @abstractmethod
    async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
        """List all tasks from the board."""

    @abstractmethod
    async def get_task(self, *, task_id: str) -> BoardTask:
        """Get a single task by ID."""

    @abstractmethod
    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        """Move a task to a new state."""

    @abstractmethod
    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        """Assign a task to an assignee."""

    @abstractmethod
    async def add_comment(self, *, task_id: str, body: str) -> None:
        """Add a comment to a task."""

    @abstractmethod
    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        """Attach an artifact (PR link, CI evidence) to a task."""
