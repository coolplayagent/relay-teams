from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class TaskId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


def new_task_id() -> TaskId:
    return TaskId(value=str(uuid4()))
