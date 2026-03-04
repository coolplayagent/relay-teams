from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import uuid4

_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar('agent_teams_trace_context', default=None)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    tool_call_id: str | None = None

    def merged(self, **updates: str | None) -> 'TraceContext':
        return TraceContext(
            trace_id=updates.get('trace_id', self.trace_id),
            request_id=updates.get('request_id', self.request_id),
            session_id=updates.get('session_id', self.session_id),
            run_id=updates.get('run_id', self.run_id),
            task_id=updates.get('task_id', self.task_id),
            instance_id=updates.get('instance_id', self.instance_id),
            role_id=updates.get('role_id', self.role_id),
            tool_call_id=updates.get('tool_call_id', self.tool_call_id),
        )


def get_trace_context() -> TraceContext:
    current = _TRACE_CONTEXT.get()
    if current is None:
        return TraceContext()
    return current


def generate_request_id() -> str:
    return f'req_{uuid4().hex[:16]}'


def generate_trace_id() -> str:
    return f'trace_{uuid4().hex[:16]}'


def set_trace_context(**updates: str | None) -> Token[TraceContext | None]:
    base = get_trace_context()
    return _TRACE_CONTEXT.set(base.merged(**updates))


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    _TRACE_CONTEXT.reset(token)


@contextmanager
def bind_trace_context(**updates: str | None):
    token = set_trace_context(**updates)
    try:
        yield get_trace_context()
    finally:
        reset_trace_context(token)
