# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500)
    status: TodoStatus

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Todo item content cannot be empty")
        return normalized


class TodoSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    items: tuple[TodoItem, ...] = ()
    version: int = Field(default=0, ge=0)
    updated_at: datetime | None = None
    updated_by_role_id: OptionalIdentifierStr = None
    updated_by_instance_id: OptionalIdentifierStr = None


def empty_todo_snapshot(
    *,
    run_id: str,
    session_id: str,
) -> TodoSnapshot:
    return TodoSnapshot(
        run_id=run_id,
        session_id=session_id,
        items=(),
        version=0,
        updated_at=None,
    )


def build_todo_snapshot(
    *,
    run_id: str,
    session_id: str,
    items: tuple[TodoItem, ...],
    version: int,
    updated_by_role_id: str | None = None,
    updated_by_instance_id: str | None = None,
) -> TodoSnapshot:
    return TodoSnapshot(
        run_id=run_id,
        session_id=session_id,
        items=items,
        version=version,
        updated_at=datetime.now(tz=timezone.utc),
        updated_by_role_id=updated_by_role_id,
        updated_by_instance_id=updated_by_instance_id,
    )
