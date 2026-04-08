# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from collections.abc import Generator
from contextlib import contextmanager
import logging
import time
from types import TracebackType
from uuid import uuid4

from relay_teams.trace.context import (
    TraceContext,
    bind_trace_context,
    generate_trace_id,
    get_trace_context,
)

type TraceExcInfo = (
    bool
    | BaseException
    | tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
    | None
)


def generate_span_id() -> str:
    return f"span_{uuid4().hex[:16]}"


@contextmanager
def trace_span(
    logger: logging.Logger,
    *,
    component: str,
    operation: str,
    attributes: dict[str, JsonValue] | None = None,
    level: int = logging.DEBUG,
    **context_updates: str | None,
) -> Generator[TraceContext, None, None]:
    parent_context = get_trace_context()
    resolved_trace_id = (
        context_updates.get("trace_id")
        or parent_context.trace_id
        or generate_trace_id()
    )
    resolved_parent_span_id = context_updates.get(
        "parent_span_id", parent_context.span_id
    )
    resolved_span_id = context_updates.get("span_id") or generate_span_id()
    resolved_context_updates = dict(context_updates)
    resolved_context_updates["trace_id"] = resolved_trace_id
    resolved_context_updates["span_id"] = resolved_span_id
    resolved_context_updates["parent_span_id"] = resolved_parent_span_id

    started = time.perf_counter()
    with bind_trace_context(**resolved_context_updates):
        current_context = get_trace_context()
        try:
            yield current_context
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _emit_trace_log(
                logger=logger,
                level=logging.ERROR,
                event="trace.span.failed",
                message=f"{component}.{operation} failed",
                component=component,
                operation=operation,
                attributes=attributes,
                duration_ms=duration_ms,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        _emit_trace_log(
            logger=logger,
            level=level,
            event="trace.span.succeeded",
            message=f"{component}.{operation} succeeded",
            component=component,
            operation=operation,
            attributes=attributes,
            duration_ms=duration_ms,
        )


def _emit_trace_log(
    *,
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    component: str,
    operation: str,
    attributes: dict[str, JsonValue] | None,
    duration_ms: int | None = None,
    exc_info: TraceExcInfo = None,
) -> None:
    payload = _build_trace_payload(
        component=component,
        operation=operation,
        attributes=attributes,
    )
    logger.log(
        level,
        message,
        extra={
            "event": event,
            "payload": payload,
            "duration_ms": duration_ms,
        },
        exc_info=exc_info,
    )


def _build_trace_payload(
    *,
    component: str,
    operation: str,
    attributes: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "component": component,
        "operation": operation,
    }
    if attributes:
        payload["attributes"] = attributes
    return payload
