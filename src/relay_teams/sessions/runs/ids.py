from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class TraceId(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str


def new_trace_id() -> TraceId:
    return TraceId(value=str(uuid4()))
