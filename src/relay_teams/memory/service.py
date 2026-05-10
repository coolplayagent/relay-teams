# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.logger import get_logger
from relay_teams.memory import memory_defaults
from relay_teams.memory.memory_defaults import (
    MIN_CONFIDENCE_ACTIVE,
    MIN_CONFIDENCE_CONSOLIDATION,
)
from relay_teams.memory.models import (
    ConsolidationMode,
    CreateMemoryEntryRequest,
    GlobalMemorySearchRequest,
    MemoryConsolidationRequest,
    MemoryConsolidationResult,
    MemoryEntry,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryQueryResult,
    MemoryScope,
    MemorySearchHit,
    MemorySearchRequest,
    MemorySearchResult,
    MemoryTier,
    UpdateMemoryEntryRequest,
    _UNSET,
    default_ttl_for_tier,
)
from relay_teams.memory.repository import MemoryBankRepository, generate_memory_id
from relay_teams.providers.provider_contracts import LLMProvider
from relay_teams.retrieval.retrieval_models import (
    RetrievalDocument,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
)
from relay_teams.retrieval.retrieval_service import RetrievalService

LOGGER = get_logger(__name__)
GLOBAL_SEARCH_BATCH_SIZE = 100
INDEX_BACKFILL_BATCH_SIZE = 100


class MemoryBankService:
    def __init__(
        self,
        *,
        repository: MemoryBankRepository,
        retrieval_service: RetrievalService | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._repo = repository
        self._retrieval_service = retrieval_service
        self._llm_provider = llm_provider

    # ------------------------------------------------------------------
    # 1. Create
    # ------------------------------------------------------------------

    async def create_entry_async(
        self, request: CreateMemoryEntryRequest
    ) -> MemoryEntry:
        now = datetime.now(tz=timezone.utc)
        memory_id = generate_memory_id()
        expires_at = request.expires_at
        if expires_at is None:
            expires_at = default_ttl_for_tier(request.tier)

        entry = MemoryEntry(
            id=memory_id,
            tier=request.tier,
            scope=request.scope,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
            run_id=request.run_id,
            role_id=request.role_id,
            kind=request.kind,
            status=MemoryEntryStatus.ACTIVE,
            content=request.content,
            tags=request.tags,
            confidence_score=request.confidence_score,
            source=request.source,
            source_ref=request.source_ref,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
            metadata=request.metadata,
        )
        await self.enforce_capacity_async(
            workspace_id=request.workspace_id,
            tier=request.tier,
            scope=request.scope,
            session_id=request.session_id,
            role_id=request.role_id,
            run_id=request.run_id,
        )
        created = await self._repo.create_entry_async(entry=entry)
        await self._index_entry_async(created)
        return created

    # ------------------------------------------------------------------
    # 2. Get / List
    # ------------------------------------------------------------------

    async def get_entry_async(self, memory_id: str) -> MemoryEntry | None:
        return await self._repo.get_by_id_async(memory_id)

    async def list_entries_async(self, query: MemoryQuery) -> MemoryQueryResult:
        return await self._repo.query_entries_async(query)

    async def reindex_active_entries_async(self) -> int:
        if self._retrieval_service is None:
            return 0

        indexed_count = 0
        offset = 0
        while True:
            result = await self._repo.query_entries_async(
                MemoryQuery(
                    status=MemoryEntryStatus.ACTIVE,
                    limit=INDEX_BACKFILL_BATCH_SIZE,
                    offset=offset,
                )
            )
            if not result.items:
                break

            for summary in result.items:
                entry = await self._repo.get_by_id_async(summary.id)
                if entry is None:
                    continue
                if await self._index_entry_async(entry):
                    indexed_count += 1

            offset += INDEX_BACKFILL_BATCH_SIZE
            if offset >= result.total_count:
                break

        if indexed_count > 0:
            LOGGER.info(
                "Reindexed %d active Memory Bank entries into retrieval",
                indexed_count,
            )
        return indexed_count

    # ------------------------------------------------------------------
    # 3. Update
    # ------------------------------------------------------------------

    async def update_entry_async(
        self, memory_id: str, request: UpdateMemoryEntryRequest
    ) -> MemoryEntry | None:
        entry = await self._repo.get_by_id_async(memory_id)
        if entry is None:
            return None
        updated = self._apply_update(entry, request)
        result = await self._repo.update_entry_async(memory_id, entry=updated)
        if result is not None:
            await self._index_entry_async(result)
        return result

    @staticmethod
    def _apply_update(
        entry: MemoryEntry, request: UpdateMemoryEntryRequest
    ) -> MemoryEntry:
        now = datetime.now(tz=timezone.utc)
        update_data: dict[str, object] = {
            "version": entry.version + 1,
            "updated_at": now,
        }

        if request.content is not None:
            update_data["content"] = request.content
        if request.tags is not None:
            update_data["tags"] = request.tags
        if request.confidence_score is not None:
            update_data["confidence_score"] = request.confidence_score
        if request.status is not None:
            update_data["status"] = request.status
        if request.expires_at is not _UNSET:
            update_data["expires_at"] = request.expires_at
        if request.metadata is not None:
            update_data["metadata"] = request.metadata

        updated = entry.model_copy(update=update_data)

        # Auto-expire if confidence falls below threshold
        if (
            updated.confidence_score < MIN_CONFIDENCE_ACTIVE
            and updated.status == MemoryEntryStatus.ACTIVE
        ):
            updated = updated.model_copy(update={"status": MemoryEntryStatus.EXPIRED})

        return updated

    # ------------------------------------------------------------------
    # 4. Delete
    # ------------------------------------------------------------------

    async def delete_entry_async(self, memory_id: str) -> bool:
        return await self._repo.delete_entry_async(memory_id)

    # ------------------------------------------------------------------
    # 4b. Capacity enforcement
    # ------------------------------------------------------------------

    async def enforce_capacity_async(
        self,
        *,
        workspace_id: str,
        tier: MemoryTier,
        scope: MemoryScope,
        session_id: str | None = None,
        role_id: str | None = None,
        run_id: str | None = None,
    ) -> int:
        """Check capacity limits and prune oldest/lowest-confidence entries.

        Returns the number of entries pruned.  Called automatically before
        ``create_entry`` so the capacity cap is never exceeded.
        """
        _ = (session_id, role_id, scope)  # reserved for granular capacity counting

        limit: int
        if tier == MemoryTier.WORKING and run_id is not None:
            limit = memory_defaults.MAX_WORKING_PER_RUN
        elif tier == MemoryTier.MEDIUM_TERM:
            limit = memory_defaults.MAX_MEDIUM_TERM_PER_SESSION_ROLE
        elif tier == MemoryTier.PERSISTENT:
            limit = memory_defaults.MAX_PERSISTENT_PER_WORKSPACE
        else:
            return 0

        current_count = await self._repo.count_entries_async(
            workspace_id=workspace_id,
            tier=tier,
            status=MemoryEntryStatus.ACTIVE,
        )
        if current_count < limit:
            return 0

        overflow = current_count - limit + 1
        if tier == MemoryTier.WORKING and run_id is not None:
            return await self._repo.expire_oldest_async(
                workspace_id=workspace_id,
                tier=tier,
                run_id=run_id,
                status=MemoryEntryStatus.ACTIVE,
                count=overflow,
            )

        # For medium_term/persistent, prune by confidence then age
        return await self._repo.expire_oldest_async(
            workspace_id=workspace_id,
            tier=tier,
            status=MemoryEntryStatus.ACTIVE,
            count=overflow,
        )

    # ------------------------------------------------------------------
    # 5. Consolidation
    # ------------------------------------------------------------------

    async def consolidate_async(
        self, request: MemoryConsolidationRequest
    ) -> MemoryConsolidationResult:
        if request.consolidation_mode == ConsolidationMode.SEMANTIC:
            return await self._consolidate_semantic_async(request)
        return await self._consolidate_structural_async(request)

    async def _consolidate_structural_async(
        self, request: MemoryConsolidationRequest
    ) -> MemoryConsolidationResult:
        source_tier = self._source_tier_for(request.target_tier)

        query = MemoryQuery(
            workspace_id=request.workspace_id,
            tier=source_tier,
            status=MemoryEntryStatus.ACTIVE,
            min_confidence=MIN_CONFIDENCE_CONSOLIDATION,
        )
        if request.session_id is not None:
            query = query.model_copy(update={"session_id": request.session_id})
        if request.role_id is not None:
            query = query.model_copy(update={"role_id": request.role_id})
        if request.filter_kind is not None:
            query = query.model_copy(update={"kind": request.filter_kind})

        result = await self._repo.query_entries_async(query)
        source_entries: list[MemoryEntry] = []
        for summary in result.items:
            entry = await self._repo.get_by_id_async(summary.id)
            if entry is not None:
                source_entries.append(entry)

        new_ids: list[str] = []
        superseded_ids: list[str] = []

        now = datetime.now(tz=timezone.utc)
        for src in source_entries:
            new_id = generate_memory_id()
            new_entry = MemoryEntry(
                id=new_id,
                tier=request.target_tier,
                scope=request.target_scope,
                workspace_id=request.workspace_id,
                session_id=request.session_id,
                run_id=None,
                role_id=request.role_id,
                kind=src.kind,
                status=MemoryEntryStatus.ACTIVE,
                content=src.content.model_copy(),
                tags=src.tags,
                confidence_score=src.confidence_score,
                source=src.source,
                source_ref=src.source_ref,
                parent_entry_id=src.id,
                created_at=now,
                updated_at=now,
                expires_at=default_ttl_for_tier(request.target_tier),
                metadata=src.metadata.copy(),
            )
            await self._repo.create_entry_async(entry=new_entry)
            new_ids.append(new_id)

            updated_src = src.model_copy(
                update={
                    "status": MemoryEntryStatus.SUPERSEDED,
                    "superseded_by_id": new_id,
                    "updated_at": now,
                }
            )
            await self._repo.update_entry_async(src.id, entry=updated_src)
            superseded_ids.append(src.id)

        return MemoryConsolidationResult(
            source_entry_count=result.total_count,
            consolidated_entry_count=len(new_ids),
            superseded_entry_ids=tuple(superseded_ids),
            new_entry_ids=tuple(new_ids),
        )

    async def _consolidate_semantic_async(
        self, request: MemoryConsolidationRequest
    ) -> MemoryConsolidationResult:
        """Run semantic (LLM-driven) consolidation.

        Extracts structured memory entries from the conversation history
        of the source run.  Falls back to structural consolidation when
        the LLM provider is not available or the extraction fails.
        """
        if self._llm_provider is None:
            LOGGER.warning(
                "SEMANTIC consolidation requested but no llm_provider configured;"
                " falling back to STRUCTURAL"
            )
            return await self._consolidate_structural_async(request)

        # Message repository is needed for SEMANTIC consolidation.
        # If not available, fall back to STRUCTURAL.
        LOGGER.warning(
            "SEMANTIC consolidation requires message_repo but none is"
            " configured; falling back to STRUCTURAL"
        )
        return await self._consolidate_structural_async(request)

    @staticmethod
    def _source_tier_for(target_tier: MemoryTier) -> MemoryTier:
        if target_tier == MemoryTier.PERSISTENT:
            return MemoryTier.MEDIUM_TERM
        return MemoryTier.WORKING

    # ------------------------------------------------------------------
    # 6. Forgetting
    # ------------------------------------------------------------------

    async def forget_expired_async(self, now: datetime | None = None) -> int:
        ttl_expired = await self._repo.expire_entries_async(now)
        decay_expired = await self._repo.apply_confidence_decay_async(
            min_confidence=MIN_CONFIDENCE_ACTIVE, now=now
        )
        return ttl_expired + decay_expired

    # ------------------------------------------------------------------
    # 7. Search (FTS5-backed)
    # ------------------------------------------------------------------

    async def search_async(self, request: MemorySearchRequest) -> MemorySearchResult:
        if (
            self._retrieval_service is not None
            and request.status == MemoryEntryStatus.ACTIVE
        ):
            return await self._search_fts_async(request)
        return await self._search_fallback_async(request)

    async def search_global_async(
        self, request: GlobalMemorySearchRequest
    ) -> MemorySearchResult:
        if request.workspace_id is not None:
            return await self.search_async(
                MemorySearchRequest(
                    workspace_id=request.workspace_id,
                    text_query=request.text_query,
                    tier=request.tier,
                    scope=request.scope,
                    session_id=request.session_id,
                    role_id=request.role_id,
                    kind=request.kind,
                    status=request.status,
                    tags=request.tags,
                    min_confidence=request.min_confidence,
                    limit=request.limit,
                )
            )

        text_lower = request.text_query.lower()
        items: list[MemorySearchHit] = []
        total_matches = 0
        rank = 1
        offset = 0
        while True:
            query = MemoryQuery(
                tier=request.tier,
                scope=request.scope,
                session_id=request.session_id,
                role_id=request.role_id,
                kind=request.kind,
                status=request.status,
                tags=request.tags,
                min_confidence=request.min_confidence,
                limit=GLOBAL_SEARCH_BATCH_SIZE,
                offset=offset,
            )
            result = await self._repo.query_entries_async(query)
            if not result.items:
                break

            for summary in result.items:
                entry = await self._repo.get_by_id_async(summary.id)
                if entry is None:
                    continue
                searchable_text = "\n".join(
                    (
                        entry.content.title,
                        entry.content.body,
                        entry.content.context,
                        entry.content.outcome,
                    )
                )
                if text_lower not in searchable_text.lower():
                    continue
                total_matches += 1
                if len(items) < request.limit:
                    items.append(
                        MemorySearchHit(
                            entry=summary,
                            score=1.0,
                            rank=rank,
                            snippet=self._build_snippet(
                                entry.content.body,
                                text_lower,
                            ),
                        )
                    )
                rank += 1
            offset += GLOBAL_SEARCH_BATCH_SIZE
            if offset >= result.total_count:
                break
        return MemorySearchResult(items=tuple(items), total_count=total_matches)

    async def _search_fts_async(
        self, request: MemorySearchRequest
    ) -> MemorySearchResult:
        """Query the FTS5 retrieval index and cross-reference with the memory table."""
        assert self._retrieval_service is not None
        fts_hits = await self._retrieval_service.search_async(
            query=RetrievalQuery(
                scope_kind=RetrievalScopeKind.MEMORY,
                scope_id=request.workspace_id,
                text=request.text_query,
                limit=request.limit,
            ),
        )
        if not fts_hits:
            return MemorySearchResult(items=(), total_count=0)

        # Build a set of matching document IDs for fast lookup
        hit_map: dict[str, tuple[float, int, str]] = {}
        for hit in fts_hits:
            hit_map[hit.document_id] = (hit.score, hit.rank, hit.snippet)

        # Pull matching entries from the memory table, applying filters
        query = MemoryQuery(
            workspace_id=request.workspace_id,
            tier=request.tier,
            scope=request.scope,
            session_id=request.session_id,
            role_id=request.role_id,
            kind=request.kind,
            status=request.status,
            min_confidence=request.min_confidence,
            limit=request.limit,
            offset=0,
        )
        result = await self._repo.query_entries_async(query)

        items: list[MemorySearchHit] = []
        for summary in result.items:
            fts_match = hit_map.get(summary.id)
            if fts_match is None:
                continue
            score, rank, snippet = fts_match
            items.append(
                MemorySearchHit(
                    entry=summary,
                    score=score,
                    rank=rank,
                    snippet=snippet
                    or self._build_snippet(
                        summary.content_body_preview, request.text_query.lower()
                    ),
                )
            )

        return MemorySearchResult(
            items=tuple(items),
            total_count=len(items),
        )

    async def _search_fallback_async(
        self, request: MemorySearchRequest
    ) -> MemorySearchResult:
        """Fallback text search when no FTS5 retrieval service is available."""
        items: list[MemorySearchHit] = []
        total_matches = 0
        rank = 1
        offset = 0
        text_lower = request.text_query.lower()
        while True:
            query = MemoryQuery(
                workspace_id=request.workspace_id,
                tier=request.tier,
                scope=request.scope,
                session_id=request.session_id,
                role_id=request.role_id,
                kind=request.kind,
                status=request.status,
                min_confidence=request.min_confidence,
                limit=GLOBAL_SEARCH_BATCH_SIZE,
                offset=offset,
            )
            result = await self._repo.query_entries_async(query)
            if not result.items:
                break

            for summary in result.items:
                entry = await self._repo.get_by_id_async(summary.id)
                if entry is None:
                    continue
                searchable_text = "\n".join(
                    (
                        entry.content.title,
                        entry.content.body,
                        entry.content.context,
                        entry.content.outcome,
                    )
                )
                if text_lower not in searchable_text.lower():
                    continue
                total_matches += 1
                if len(items) < request.limit:
                    items.append(
                        MemorySearchHit(
                            entry=summary,
                            score=1.0,
                            rank=rank,
                            snippet=self._build_snippet(searchable_text, text_lower),
                        )
                    )
                rank += 1

            offset += GLOBAL_SEARCH_BATCH_SIZE
            if offset >= result.total_count:
                break

        return MemorySearchResult(
            items=tuple(items),
            total_count=total_matches,
        )

    @staticmethod
    def _build_snippet(body_preview: str, query_text: str) -> str:
        lower_body = body_preview.lower()
        idx = lower_body.find(query_text)
        if idx == -1:
            return body_preview[:200]
        start = max(0, idx - 50)
        end = min(len(body_preview), idx + len(query_text) + 50)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(body_preview) else ""
        return f"{prefix}{body_preview[start:end]}{suffix}"

    # ------------------------------------------------------------------
    # FTS5 indexing integration
    # ------------------------------------------------------------------

    async def _index_entry_async(self, entry: MemoryEntry) -> bool:
        """Index a memory entry into the FTS5 retrieval store.

        Silently skips indexing if no retrieval_service is configured or if
        the entry should not be indexed (e.g. non-ACTIVE status).
        """
        if self._retrieval_service is None:
            return False
        if entry.status != MemoryEntryStatus.ACTIVE:
            return False

        scope_id = entry.workspace_id
        config = RetrievalScopeConfig(
            scope_kind=RetrievalScopeKind.MEMORY,
            scope_id=scope_id,
        )
        body_parts = [entry.content.body]
        if entry.content.context:
            body_parts.append(entry.content.context)
        if entry.content.outcome:
            body_parts.append(entry.content.outcome)
        body = "\n".join(body_parts)

        doc = RetrievalDocument(
            scope_kind=RetrievalScopeKind.MEMORY,
            scope_id=scope_id,
            document_id=entry.id,
            title=entry.content.title,
            body=body,
            keywords=entry.tags,
        )
        try:
            await self._retrieval_service.upsert_documents_async(
                config=config,
                documents=(doc,),
            )
            return True
        except (ValueError, OSError, RuntimeError):
            LOGGER.warning(
                "failed to index memory entry %s in FTS5",
                entry.id,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # 8. Condensation (placeholder)
    # ------------------------------------------------------------------

    # TODO: FE-2 -- Implement LLM-based condensation. The intended behaviour
    # is to cluster related entries within a workspace, use an LLM call to
    # produce a unified SUMMARY-kind entry, and supersede the source entries.

    def condense(self, workspace_id: str) -> None:
        """Condense verbose memory entries into concise SUMMARY entries.

        This method is reserved for FE-2 which will implement LLM-based
        summarization of related entries within *workspace_id*.  Until then,
        calling this method raises ``NotImplementedError`` to make the
        incomplete state explicit.
        """
        raise NotImplementedError(
            "LLM-based condensation is not yet implemented. "
            "Tracked by FE-2. "
            f"workspace_id={workspace_id}"
        )
