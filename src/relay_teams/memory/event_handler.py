# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.logger import get_logger
from relay_teams.memory.models import (
    ConsolidationMode,
    CreateMemoryEntryRequest,
    MemoryConsolidationRequest,
    MemoryContent,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_service import RoleMemoryService

LOGGER = get_logger(__name__)


class MemoryEventHandler:
    """Coordinates lifecycle-driven memory bank operations.

    Handles three consolidation triggers:
    - task success  -> create WORKING entry + dual-write to legacy role_memories
    - run completion -> consolidate WORKING -> MEDIUM_TERM
    - session completion -> consolidate MEDIUM_TERM -> PERSISTENT
    """

    def __init__(
        self,
        *,
        memory_bank_service: MemoryBankService,
        role_memory_service: RoleMemoryService | None = None,
    ) -> None:
        self._memory_bank = memory_bank_service
        self._role_memory_service = role_memory_service

    def on_task_completed(
        self,
        *,
        workspace_id: str,
        role_id: str,
        session_id: str,
        run_id: str,
        task_id: str,
        objective: str,
        result: str,
    ) -> None:
        """Create a WORKING memory entry for a completed task.

        Also performs a dual-write to the legacy role_memories table so that
        ``build_injected_memory()`` continues to work during the migration
        period.
        """
        content = MemoryContent(
            title=objective[:500] if objective else f"Task {task_id}",
            body=result if result else "(no result)",
            context=f"task_id={task_id} session_id={session_id}",
            outcome="completed",
        )
        request = CreateMemoryEntryRequest(
            tier=MemoryTier.WORKING,
            scope=MemoryScope.WORKSPACE,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            role_id=role_id,
            kind=MemoryEntryKind.SUMMARY,
            content=content,
            source=MemorySourceKind.TASK_RESULT,
            source_ref=task_id,
        )
        try:
            self._memory_bank.create_entry(request)
        except (ValueError, OSError, RuntimeError):
            LOGGER.warning(
                "failed to create WORKING memory entry for task %s",
                task_id,
                exc_info=True,
            )

        # Dual-write bridge: also record in legacy role_memories
        if self._role_memory_service is not None:
            try:
                self._role_memory_service.record_task_result(
                    role_id=role_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    task_id=task_id,
                    objective=objective,
                    result=result,
                    transcript_lines=(),
                )
            except (ValueError, OSError, RuntimeError):
                LOGGER.warning(
                    "failed to dual-write task result to role_memories for task %s",
                    task_id,
                    exc_info=True,
                )

    def on_run_completed(
        self,
        *,
        workspace_id: str,
        session_id: str,
        role_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Consolidate WORKING entries -> MEDIUM_TERM on run completion."""
        request = MemoryConsolidationRequest(
            workspace_id=workspace_id,
            session_id=session_id,
            role_id=role_id,
            source_run_id=run_id,
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION if session_id else MemoryScope.ROLE,
        )
        try:
            result = self._memory_bank.consolidate(request)
            if result.source_entry_count > 0:
                LOGGER.info(
                    "run consolidation: %d WORKING -> %d MEDIUM_TERM "
                    "workspace=%s session=%s",
                    result.source_entry_count,
                    result.consolidated_entry_count,
                    workspace_id,
                    session_id,
                )
        except (ValueError, OSError, RuntimeError):
            LOGGER.warning(
                "failed to consolidate WORKING->MEDIUM_TERM workspace=%s session=%s",
                workspace_id,
                session_id,
                exc_info=True,
            )

    async def on_run_completed_async(
        self,
        *,
        workspace_id: str,
        session_id: str,
        role_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Consolidate WORKING entries -> MEDIUM_TERM on run completion.

        Performs structural consolidation (sync) and then additionally
        triggers SEMANTIC mode consolidation for high-signal extraction.
        SEMANTIC failures do not affect the structural path.
        """
        # 1. Structural consolidation (same as sync version)
        structural_request = MemoryConsolidationRequest(
            workspace_id=workspace_id,
            session_id=session_id,
            role_id=role_id,
            source_run_id=run_id,
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION if session_id else MemoryScope.ROLE,
        )
        try:
            result = await self._memory_bank.consolidate_async(structural_request)
            if result.source_entry_count > 0:
                LOGGER.info(
                    "run consolidation: %d WORKING -> %d MEDIUM_TERM "
                    "workspace=%s session=%s",
                    result.source_entry_count,
                    result.consolidated_entry_count,
                    workspace_id,
                    session_id,
                )
        except (ValueError, OSError, RuntimeError):
            LOGGER.warning(
                "failed to consolidate WORKING->MEDIUM_TERM workspace=%s session=%s",
                workspace_id,
                session_id,
                exc_info=True,
            )

        # 2. Semantic consolidation (best-effort, does not affect structural)
        if run_id is not None:
            semantic_request = MemoryConsolidationRequest(
                workspace_id=workspace_id,
                session_id=session_id,
                role_id=role_id,
                source_run_id=run_id,
                target_tier=MemoryTier.MEDIUM_TERM,
                target_scope=MemoryScope.SESSION if session_id else MemoryScope.ROLE,
                consolidation_mode=ConsolidationMode.SEMANTIC,
                max_extracted_entries=15,
                extraction_kinds=(
                    MemoryEntryKind.DECISION,
                    MemoryEntryKind.FAILURE_MODE,
                    MemoryEntryKind.CONSTRAINT,
                    MemoryEntryKind.INSIGHT,
                ),
            )
            try:
                semantic_result = await self._memory_bank.consolidate_async(
                    semantic_request
                )
                if semantic_result.consolidated_entry_count > 0:
                    LOGGER.info(
                        "semantic consolidation: %d entries extracted"
                        " from run=%s (tokens=%d, duration=%dms)",
                        semantic_result.consolidated_entry_count,
                        run_id,
                        semantic_result.extraction_tokens_used,
                        semantic_result.extraction_duration_ms,
                    )
            except (ValueError, OSError, RuntimeError):
                LOGGER.warning(
                    "semantic consolidation failed for run=%s (non-fatal)",
                    run_id,
                    exc_info=True,
                )

    def on_session_completed(
        self,
        *,
        workspace_id: str,
        session_id: str,
        role_id: str | None = None,
    ) -> None:
        """Consolidate MEDIUM_TERM entries -> PERSISTENT on session end."""
        request = MemoryConsolidationRequest(
            workspace_id=workspace_id,
            session_id=session_id,
            role_id=role_id,
            target_tier=MemoryTier.PERSISTENT,
            target_scope=MemoryScope.WORKSPACE,
        )
        try:
            result = self._memory_bank.consolidate(request)
            if result.source_entry_count > 0:
                LOGGER.info(
                    "session consolidation: %d MEDIUM_TERM -> %d PERSISTENT "
                    "workspace=%s session=%s",
                    result.source_entry_count,
                    result.consolidated_entry_count,
                    workspace_id,
                    session_id,
                )
        except (ValueError, OSError, RuntimeError):
            LOGGER.warning(
                "failed to consolidate MEDIUM_TERM->PERSISTENT workspace=%s session=%s",
                workspace_id,
                session_id,
                exc_info=True,
            )

    def get_injectable_memory_text(
        self,
        *,
        workspace_id: str,
        role_id: str | None = None,
    ) -> str:
        """Build injectable memory text from PERSISTENT and MEDIUM_TERM entries.

        Used by prompt assembly to include memory bank content alongside
        the legacy reflection memory section.
        """
        lines: list[str] = []
        for tier in (MemoryTier.PERSISTENT, MemoryTier.MEDIUM_TERM):
            query = MemoryQuery(
                workspace_id=workspace_id,
                tier=tier,
                role_id=role_id,
                status=MemoryEntryStatus.ACTIVE,
                limit=20,
            )
            try:
                result = self._memory_bank.list_entries(query)
            except (ValueError, OSError, RuntimeError):
                LOGGER.warning(
                    "failed to query %s memory for injection workspace=%s",
                    tier.value,
                    workspace_id,
                    exc_info=True,
                )
                continue
            if not result.items:
                continue
            tier_label = tier.value.replace("_", " ").title()
            lines.append(f"### {tier_label}")
            for entry in result.items:
                lines.append(f"- [{entry.kind.value}] {entry.content_title}")
        return "\n".join(lines)
