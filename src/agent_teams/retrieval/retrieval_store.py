# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from agent_teams.retrieval.retrieval_models import (
    RetrievalBackendKind,
    RetrievalDocument,
    RetrievalHit,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalStats,
)


class RetrievalStore(Protocol):
    @property
    def backend_kind(self) -> RetrievalBackendKind: ...

    def replace_scope(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats: ...

    def upsert_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats: ...

    def delete_documents(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> RetrievalStats: ...

    def search(
        self,
        *,
        query: RetrievalQuery,
    ) -> tuple[RetrievalHit, ...]: ...

    def rebuild_scope(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats: ...

    def stats(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats: ...
