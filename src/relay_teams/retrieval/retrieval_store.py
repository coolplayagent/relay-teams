# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from relay_teams.retrieval.retrieval_models import (
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
    def backend_kind(self) -> RetrievalBackendKind:
        raise NotImplementedError

    def replace_scope(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        raise NotImplementedError  # pragma: no cover

    def upsert_documents(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        raise NotImplementedError  # pragma: no cover

    async def upsert_documents_async(
        self,
        *,
        config: RetrievalScopeConfig,
        documents: tuple[RetrievalDocument, ...],
    ) -> RetrievalStats:
        raise NotImplementedError  # pragma: no cover

    def delete_documents(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
        document_ids: tuple[str, ...],
    ) -> RetrievalStats:
        raise NotImplementedError  # pragma: no cover

    def search(
        self,
        *,
        query: RetrievalQuery,
    ) -> tuple[RetrievalHit, ...]:
        raise NotImplementedError  # pragma: no cover

    async def search_async(
        self,
        *,
        query: RetrievalQuery,
    ) -> tuple[RetrievalHit, ...]:
        raise NotImplementedError  # pragma: no cover

    def rebuild_scope(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        raise NotImplementedError  # pragma: no cover

    def stats(
        self,
        *,
        scope_kind: RetrievalScopeKind,
        scope_id: str,
    ) -> RetrievalStats:
        raise NotImplementedError  # pragma: no cover
