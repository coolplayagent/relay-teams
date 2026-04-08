# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "agent_teams_trace_context", default=None
)


class TraceContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    _field_names: ClassVar[frozenset[str]]

    trace_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    trigger_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    tool_call_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def merged(self, **updates: str | None) -> TraceContext:
        _validate_trace_updates(updates)
        merged_data = self.model_dump()
        for key, value in updates.items():
            merged_data[key] = value
        return TraceContext.model_validate(merged_data)


def get_trace_context() -> TraceContext:
    current = _TRACE_CONTEXT.get()
    if current is None:
        return TraceContext()
    return current


def generate_request_id() -> str:
    return f"req_{uuid4().hex[:16]}"


def generate_trace_id() -> str:
    return f"trace_{uuid4().hex[:16]}"


def set_trace_context(**updates: str | None) -> Token[TraceContext | None]:
    base = get_trace_context()
    return _TRACE_CONTEXT.set(base.merged(**updates))


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    _TRACE_CONTEXT.reset(token)


@contextmanager
def bind_trace_context(**updates: str | None) -> Generator[TraceContext, None, None]:
    token = set_trace_context(**updates)
    try:
        yield get_trace_context()
    finally:
        reset_trace_context(token)


TraceContext._field_names = frozenset(TraceContext.model_fields.keys())


def _validate_trace_updates(updates: dict[str, str | None]) -> None:
    unknown = sorted(key for key in updates if key not in TraceContext._field_names)
    if unknown:
        raise ValueError(f"Unknown trace context fields: {unknown}")
