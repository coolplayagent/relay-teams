from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class InstanceId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


def new_instance_id() -> InstanceId:
    return InstanceId(value=str(uuid4()))
