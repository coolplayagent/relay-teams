# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.metrics.definitions import (
    RETRIEVAL_DOCUMENT_COUNT,
    RETRIEVAL_REBUILDS,
    RETRIEVAL_REBUILD_DURATION_MS,
    RETRIEVAL_SEARCHES,
    RETRIEVAL_SEARCH_DURATION_MS,
    RETRIEVAL_SEARCH_FAILURES,
)
from relay_teams.metrics.models import MetricTagSet
from relay_teams.metrics.recorder import MetricRecorder
from relay_teams.trace import get_trace_context


def record_retrieval_search(
    recorder: MetricRecorder,
    *,
    backend: str,
    scope_kind: str,
    duration_ms: int,
    success: bool,
) -> None:
    tags = _build_tags(
        backend=backend,
        scope_kind=scope_kind,
        operation="search",
        status="success" if success else "failure",
    )
    recorder.emit(definition_name=RETRIEVAL_SEARCHES.name, value=1, tags=tags)
    recorder.emit(
        definition_name=RETRIEVAL_SEARCH_DURATION_MS.name,
        value=duration_ms,
        tags=tags,
    )
    if not success:
        recorder.emit(
            definition_name=RETRIEVAL_SEARCH_FAILURES.name,
            value=1,
            tags=tags,
        )


def record_retrieval_rebuild(
    recorder: MetricRecorder,
    *,
    backend: str,
    scope_kind: str,
    duration_ms: int,
    success: bool,
) -> None:
    tags = _build_tags(
        backend=backend,
        scope_kind=scope_kind,
        operation="rebuild",
        status="success" if success else "failure",
    )
    recorder.emit(definition_name=RETRIEVAL_REBUILDS.name, value=1, tags=tags)
    recorder.emit(
        definition_name=RETRIEVAL_REBUILD_DURATION_MS.name,
        value=duration_ms,
        tags=tags,
    )


def record_retrieval_document_count(
    recorder: MetricRecorder,
    *,
    backend: str,
    scope_kind: str,
    operation: str,
    document_count: int,
) -> None:
    recorder.emit(
        definition_name=RETRIEVAL_DOCUMENT_COUNT.name,
        value=document_count,
        tags=_build_tags(
            backend=backend,
            scope_kind=scope_kind,
            operation=operation,
            status="success",
        ),
    )


def _build_tags(
    *,
    backend: str,
    scope_kind: str,
    operation: str,
    status: str,
) -> MetricTagSet:
    trace_context = get_trace_context()
    return MetricTagSet(
        session_id=trace_context.session_id or "",
        run_id=trace_context.run_id or "",
        instance_id=trace_context.instance_id or "",
        role_id=trace_context.role_id or "",
        retrieval_backend=backend,
        retrieval_scope_kind=scope_kind,
        retrieval_operation=operation,
        status=status,
    )
