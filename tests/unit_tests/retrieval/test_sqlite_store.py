# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.retrieval import (
    RetrievalDocument,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalTokenizer,
    SqliteFts5RetrievalStore,
)


def test_sqlite_store_replaces_scope_and_reuses_index_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retrieval.db"
    store = SqliteFts5RetrievalStore(db_path)
    config = RetrievalScopeConfig(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
    )

    stats = store.replace_scope(
        config=config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="skill-router",
                title="Skill Router",
                body="Body aware routing for large skill catalogs",
                keywords=("skill", "routing"),
            ),
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="memory-notes",
                title="Memory Notes",
                body="Reflection memory storage and reuse",
                keywords=("memory",),
            ),
        ),
    )

    assert stats.document_count == 2
    assert stats.tokenizer == RetrievalTokenizer.UNICODE61
    hits = store.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
            text="skill routing",
            limit=5,
        )
    )
    assert [hit.document_id for hit in hits] == ["skill-router"]

    reopened = SqliteFts5RetrievalStore(db_path)
    reopened_hits = reopened.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
            text="skill routing",
            limit=5,
        )
    )
    assert [hit.document_id for hit in reopened_hits] == ["skill-router"]


def test_sqlite_store_isolates_scopes_and_supports_trigram_queries(
    tmp_path: Path,
) -> None:
    store = SqliteFts5RetrievalStore(tmp_path / "scope-isolation.db")
    store.replace_scope(
        config=RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
        ),
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="skill-router",
                title="Skill Router",
                body="Routes body aware skills",
                keywords=("routing",),
            ),
        ),
    )
    store.replace_scope(
        config=RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind.MEMORY,
            scope_id="memories",
            tokenizer=RetrievalTokenizer.TRIGRAM,
        ),
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.MEMORY,
                scope_id="memories",
                document_id="reflection-1",
                title="中文反思",
                body="这是中文检索测试能力样本",
                keywords=("中文检索",),
            ),
        ),
    )

    skill_hits = store.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
            text="中文检索",
            limit=5,
        )
    )
    memory_hits = store.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.MEMORY,
            scope_id="memories",
            text="中文检索",
            limit=5,
        )
    )

    assert skill_hits == ()
    assert [hit.document_id for hit in memory_hits] == ["reflection-1"]


def test_sqlite_store_upsert_and_delete_keep_indexes_in_sync(tmp_path: Path) -> None:
    store = SqliteFts5RetrievalStore(tmp_path / "upsert-delete.db")
    config = RetrievalScopeConfig(
        scope_kind=RetrievalScopeKind.MCP,
        scope_id="tools",
    )
    store.replace_scope(
        config=config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.MCP,
                scope_id="tools",
                document_id="tool-a",
                title="Tool A",
                body="reads files safely",
                keywords=("read",),
            ),
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.MCP,
                scope_id="tools",
                document_id="tool-b",
                title="Tool B",
                body="writes reports",
                keywords=("write",),
            ),
        ),
    )

    store.upsert_documents(
        config=config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.MCP,
                scope_id="tools",
                document_id="tool-b",
                title="Tool B",
                body="writes reports and reads files safely",
                keywords=("write", "read"),
            ),
        ),
    )
    hits_after_upsert = store.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.MCP,
            scope_id="tools",
            text="read files",
            limit=5,
        )
    )
    assert [hit.document_id for hit in hits_after_upsert] == ["tool-a", "tool-b"]

    stats = store.delete_documents(
        scope_kind=RetrievalScopeKind.MCP,
        scope_id="tools",
        document_ids=("tool-a",),
    )
    hits_after_delete = store.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.MCP,
            scope_id="tools",
            text="read files",
            limit=5,
        )
    )
    assert stats.document_count == 1
    assert [hit.document_id for hit in hits_after_delete] == ["tool-b"]


def test_sqlite_store_applies_title_weight_to_ranking(tmp_path: Path) -> None:
    store = SqliteFts5RetrievalStore(tmp_path / "weights.db")
    store.replace_scope(
        config=RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind.FILE,
            scope_id="workspace",
            title_weight=10.0,
            body_weight=1.0,
            keyword_weight=1.0,
        ),
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.FILE,
                scope_id="workspace",
                document_id="title-match",
                title="Router Guide",
                body="general notes",
            ),
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.FILE,
                scope_id="workspace",
                document_id="body-match",
                title="General Guide",
                body="router router router implementation details",
            ),
        ),
    )

    hits = store.search(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.FILE,
            scope_id="workspace",
            text="router",
            limit=5,
        )
    )
    assert hits[0].document_id == "title-match"


def test_sqlite_store_requires_fts5_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.retrieval.sqlite_store.sqlite_supports_fts5",
        lambda _conn: False,
    )

    with pytest.raises(RuntimeError, match="SQLite FTS5 is required"):
        SqliteFts5RetrievalStore(tmp_path / "missing-fts5.db")
