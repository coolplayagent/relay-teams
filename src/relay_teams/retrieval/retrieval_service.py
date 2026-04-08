# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from relay_teams.logger import get_logger
from relay_teams.metrics import MetricRecorder
from relay_teams.metrics.adapters import (
    record_retrieval_document_count,
    record_retrieval_rebuild,
    record_retrieval_search,
)
from relay_teams.retrieval.retrieval_models import (
    RetrievalDocument,
    RetrievalHit,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalStats,
)
from relay_teams.retrieval.retrieval_store import RetrievalStore
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)


class RetrievalService:
    def __init__(
        self,
        *,
        store: RetrievalStore,
        metric_recorder: MetricRecorder | None = None,
    ) -> None:
        self._store = store
        self._metric_recorder = metric_recorder

    def replace_scope(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        self._validate_scope_documents(config=config, documents=documents)
        with trace_span(
            LOGGER,
            component="retrieval.service",
            operation="replace_scope",
            attributes={
                "backend": self._store.backend_kind.value,
                "scope_kind": config.scope_kind.value,
                "scope_id": config.scope_id,
                "document_count": len(documents),
            },
        ):
            stats = self._store.replace_scope(config=config, documents=documents)
        self._record_document_count(stats=stats, operation="replace")
        return stats

    def upsert_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        self._validate_scope_documents(config=config, documents=documents)
        with trace_span(
            LOGGER,
            component="retrieval.service",
            operation="upsert_documents",
            attributes={
                "backend": self._store.backend_kind.value,
                "scope_kind": config.scope_kind.value,
                "scope_id": config.scope_id,
                "document_count": len(documents),
            },
        ):
            stats = self._store.upsert_documents(config=config, documents=documents)
        self._record_document_count(stats=stats, operation="upsert")
        return stats

    def delete_documents(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> RetrievalStats:
        normalized_document_ids = _deduplicate_ids(document_ids)
        with trace_span(
            LOGGER,
            component="retrieval.service",
            operation="delete_documents",
            attributes={
                "backend": self._store.backend_kind.value,
                "scope_kind": scope_kind.value,
                "scope_id": scope_id,
                "document_count": len(normalized_document_ids),
            },
        ):
            stats = self._store.delete_documents(
                scope_kind=scope_kind,
                scope_id=scope_id,
                document_ids=normalized_document_ids,
            )
        self._record_document_count(stats=stats, operation="delete")
        return stats

    def search(
        self,
        *,
        query: RetrievalQuery,
    ) -> tuple[RetrievalHit, ...]:
        started = time.perf_counter()
        success = False
        with trace_span(
            LOGGER,
            component="retrieval.service",
            operation="search",
            attributes={
                "backend": self._store.backend_kind.value,
                "scope_kind": query.scope_kind.value,
                "scope_id": query.scope_id,
                "limit": query.limit,
                "query_term_count": _query_term_count(query.text),
            },
        ):
            try:
                result = self._store.search(query=query)
                success = True
                return result
            finally:
                self._record_search_metric(
                    scope_kind=query.scope_kind,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    success=success,
                )

    def rebuild_scope(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        started = time.perf_counter()
        success = False
        with trace_span(
            LOGGER,
            component="retrieval.service",
            operation="rebuild_scope",
            attributes={
                "backend": self._store.backend_kind.value,
                "scope_kind": scope_kind.value,
                "scope_id": scope_id,
            },
        ):
            try:
                stats = self._store.rebuild_scope(
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                )
                success = True
            finally:
                self._record_rebuild_metric(
                    scope_kind=scope_kind,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    success=success,
                )
        self._record_document_count(stats=stats, operation="rebuild")
        return stats

    def stats(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        with trace_span(
            LOGGER,
            component="retrieval.service",
            operation="stats",
            attributes={
                "backend": self._store.backend_kind.value,
                "scope_kind": scope_kind.value,
                "scope_id": scope_id,
            },
        ):
            return self._store.stats(scope_kind=scope_kind, scope_id=scope_id)

    def _record_search_metric(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        duration_ms: int,
        success: bool,
    ) -> None:
        if self._metric_recorder is None:
            return
        record_retrieval_search(
            self._metric_recorder,
            backend=self._store.backend_kind.value,
            scope_kind=scope_kind.value,
            duration_ms=duration_ms,
            success=success,
        )

    def _record_rebuild_metric(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        duration_ms: int,
        success: bool,
    ) -> None:
        if self._metric_recorder is None:
            return
        record_retrieval_rebuild(
            self._metric_recorder,
            backend=self._store.backend_kind.value,
            scope_kind=scope_kind.value,
            duration_ms=duration_ms,
            success=success,
        )

    def _record_document_count(
        self,
        *,
        stats: RetrievalStats,
        operation: str,
    ) -> None:
        if self._metric_recorder is None:
            return
        record_retrieval_document_count(
            self._metric_recorder,
            backend=stats.backend.value,
            scope_kind=stats.scope_kind.value,
            operation=operation,
            document_count=stats.document_count,
        )

    def _validate_scope_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> None:
        for document in documents:
            if (
                document.scope_kind != config.scope_kind
                or document.scope_id != config.scope_id
            ):
                raise ValueError(
                    "All retrieval documents must match the target scope config"
                )


def _deduplicate_ids(document_ids: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for document_id in document_ids:
        normalized = document_id.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def _query_term_count(raw_query: str) -> int:
    return len([part for part in raw_query.split() if part.strip()])
