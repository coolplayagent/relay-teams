# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

import pytest

from relay_teams.metrics import (
    DEFAULT_DEFINITIONS,
    MetricRecorder,
    MetricRegistry,
    MetricScope,
    SqliteMetricAggregateStore,
)
from relay_teams.metrics.sinks import AggregateStoreSink
from relay_teams.retrieval import (
    RetrievalBackendKind,
    RetrievalDocument,
    RetrievalHit,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalService,
    RetrievalStats,
)
from relay_teams.trace import bind_trace_context


class _FakeRetrievalStore:
    def __init__(self) -> None:
        self._scope_stats: dict[tuple[RetrievalScopeKind, str], RetrievalStats] = {}
        self.search_result: tuple[RetrievalHit, ...] = (
            RetrievalHit(
                document_id="skill-router",
                score=1.0,
                rank=1,
                title="Skill Router",
                snippet="body aware routing",
            ),
        )
        self.raise_on_search = False

    @property
    def backend_kind(self) -> RetrievalBackendKind:
        return RetrievalBackendKind.SQLITE_FTS5

    def replace_scope(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        stats = RetrievalStats(
            scope_kind=config.scope_kind,
            scope_id=config.scope_id,
            backend=self.backend_kind,
            tokenizer=config.tokenizer,
            document_count=len(documents),
            updated_at=datetime.now(tz=timezone.utc),
        )
        self._scope_stats[(config.scope_kind, config.scope_id)] = stats
        return stats

    def upsert_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        current = self._scope_stats.get(
            (config.scope_kind, config.scope_id),
            RetrievalStats(
                scope_kind=config.scope_kind,
                scope_id=config.scope_id,
                backend=self.backend_kind,
                tokenizer=config.tokenizer,
                document_count=0,
                updated_at=None,
            ),
        )
        stats = current.model_copy(
            update={
                "tokenizer": config.tokenizer,
                "document_count": current.document_count + len(documents),
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        self._scope_stats[(config.scope_kind, config.scope_id)] = stats
        return stats

    def delete_documents(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> RetrievalStats:
        current = self._scope_stats[(scope_kind, scope_id)]
        stats = current.model_copy(
            update={
                "document_count": max(0, current.document_count - len(document_ids)),
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        self._scope_stats[(scope_kind, scope_id)] = stats
        return stats

    def search(
        self,
        *,
        query: RetrievalQuery,
    ) -> tuple[RetrievalHit, ...]:
        del query
        if self.raise_on_search:
            raise RuntimeError("search failed")
        return self.search_result

    def rebuild_scope(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        current = self._scope_stats[(scope_kind, scope_id)]
        stats = current.model_copy(update={"updated_at": datetime.now(tz=timezone.utc)})
        self._scope_stats[(scope_kind, scope_id)] = stats
        return stats

    def stats(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        return self._scope_stats[(scope_kind, scope_id)]


def test_retrieval_service_emits_metrics_and_trace_without_leaking_query_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    metric_store = SqliteMetricAggregateStore(tmp_path / "retrieval-metrics.db")
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(AggregateStoreSink(metric_store),),
    )
    service = RetrievalService(store=_seeded_fake_store(), metric_recorder=recorder)

    with bind_trace_context(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="coordinator",
    ):
        with caplog.at_level(
            logging.DEBUG,
            logger="relay_teams.backend.retrieval.retrieval_service",
        ):
            hits = service.search(
                query=RetrievalQuery(
                    scope_kind=RetrievalScopeKind.SKILL,
                    scope_id="skills",
                    text="secret routing phrase",
                    limit=5,
                )
            )

    assert [hit.document_id for hit in hits] == ["skill-router"]
    search_logs = [
        payload
        for record in caplog.records
        for payload in [getattr(record, "payload", None)]
        if getattr(record, "event", None) == "trace.span.succeeded"
        and isinstance(payload, dict)
        and payload.get("operation") == "search"
    ]
    assert len(search_logs) == 1
    payload = search_logs[0]
    assert payload["attributes"]["query_term_count"] == 3
    assert "secret routing phrase" not in str(payload)

    points = metric_store.query_points(
        scope=MetricScope.SESSION,
        scope_id="session-1",
        time_window_minutes=60,
    )
    recorded_metric_names = {point.metric_name for point in points}
    assert "relay_teams.retrieval.searches" in recorded_metric_names
    assert "relay_teams.retrieval.search_duration_ms" in recorded_metric_names
    assert any(
        '"retrieval_scope_kind": "skill"' in point.tags_json
        and '"retrieval_operation": "search"' in point.tags_json
        and '"status": "success"' in point.tags_json
        for point in points
    )


def test_retrieval_service_records_failure_metrics_for_search(tmp_path: Path) -> None:
    metric_store = SqliteMetricAggregateStore(tmp_path / "retrieval-failure.db")
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(AggregateStoreSink(metric_store),),
    )
    fake_store = _seeded_fake_store()
    fake_store.raise_on_search = True
    service = RetrievalService(store=fake_store, metric_recorder=recorder)

    with bind_trace_context(session_id="session-1", run_id="run-1"):
        with pytest.raises(RuntimeError, match="search failed"):
            service.search(
                query=RetrievalQuery(
                    scope_kind=RetrievalScopeKind.SKILL,
                    scope_id="skills",
                    text="routing",
                    limit=5,
                )
            )

    points = metric_store.query_points(
        scope=MetricScope.SESSION,
        scope_id="session-1",
        time_window_minutes=60,
    )
    assert any(
        point.metric_name == "relay_teams.retrieval.search_failures" for point in points
    )
    assert any('"status": "failure"' in point.tags_json for point in points)


def test_retrieval_service_records_document_count_gauge(tmp_path: Path) -> None:
    metric_store = SqliteMetricAggregateStore(tmp_path / "retrieval-document-count.db")
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(AggregateStoreSink(metric_store),),
    )
    service = RetrievalService(store=_seeded_fake_store(), metric_recorder=recorder)

    service.replace_scope(
        config=RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
        ),
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="a",
                title="A",
            ),
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="b",
                title="B",
            ),
        ),
    )

    points = metric_store.query_points(
        scope=MetricScope.GLOBAL,
        scope_id="global",
        time_window_minutes=60,
    )
    assert any(
        point.metric_name == "relay_teams.retrieval.document_count" and point.value == 2
        for point in points
    )


def test_retrieval_service_rejects_mismatched_scope_documents() -> None:
    service = RetrievalService(store=_seeded_fake_store())

    with pytest.raises(ValueError, match="must match the target scope config"):
        service.replace_scope(
            config=RetrievalScopeConfig(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
            ),
            documents=(
                RetrievalDocument(
                    scope_kind=RetrievalScopeKind.MEMORY,
                    scope_id="memories",
                    document_id="mismatch",
                    title="Mismatch",
                ),
            ),
        )


def _seeded_fake_store() -> _FakeRetrievalStore:
    store = _FakeRetrievalStore()
    store.replace_scope(
        config=RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
        ),
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="seed",
                title="Seed",
            ),
        ),
    )
    return store
