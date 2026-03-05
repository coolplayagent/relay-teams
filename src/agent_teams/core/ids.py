from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class RoleId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


class InstanceId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


class TaskId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


class WorkflowId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


class TraceId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


def new_trace_id() -> TraceId:
    return TraceId(value=str(uuid4()))


def new_instance_id() -> InstanceId:
    return InstanceId(value=str(uuid4()))


def new_task_id() -> TaskId:
    return TaskId(value=str(uuid4()))
