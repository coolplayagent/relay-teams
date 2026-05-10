# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from relay_teams.agents.tasks.models import VerificationReport
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
    UpdateMemoryEntryRequest,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_models import (
    PerformanceTrendPoint,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)

LOGGER = get_logger(__name__)


class MemoryEventHandler:
    """Coordinates lifecycle-driven memory bank operations.

    Handles three consolidation triggers:
    - task success  -> create WORKING entry
    - run completion -> consolidate WORKING -> MEDIUM_TERM
    - session completion -> consolidate MEDIUM_TERM -> PERSISTENT
    """

    def __init__(
        self,
        *,
        memory_bank_service: MemoryBankService,
    ) -> None:
        self._memory_bank = memory_bank_service

    async def on_task_completed_async(
        self,
        *,
        workspace_id: str,
        role_id: str,
        session_id: str,
        run_id: str,
        task_id: str,
        objective: str,
        result: str,
        verification_report: VerificationReport | None = None,
    ) -> None:
        """Create a WORKING memory entry for a completed task."""
        outcome_parts = ["completed"]
        if verification_report is not None:
            outcome_parts.append(
                "verification=passed"
                if verification_report.passed
                else "verification=failed"
            )
        content = MemoryContent(
            title=objective[:500] if objective else f"Task {task_id}",
            body=result if result else "(no result)",
            context=f"task_id={task_id} session_id={session_id}",
            outcome=" ".join(outcome_parts),
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
            await self._memory_bank.create_entry_async(request)
        except (ValueError, OSError, RuntimeError, sqlite3.Error):
            LOGGER.warning(
                "failed to create WORKING memory entry for task %s",
                task_id,
                exc_info=True,
            )

        if verification_report is not None:
            try:
                await self._record_verification_outcome_async(
                    role_id=role_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    verification_report=verification_report,
                )
            except (ValueError, OSError, RuntimeError, sqlite3.Error):
                LOGGER.warning(
                    "failed to record role performance for task %s",
                    task_id,
                    exc_info=True,
                )

    async def _record_verification_outcome_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task_id: str,
        verification_report: VerificationReport,
    ) -> None:
        entry_id, performance = await self._get_role_performance_async(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        updated = _apply_verification_report(
            role_id=role_id,
            workspace_id=workspace_id,
            performance=performance,
            verification_report=verification_report,
        )
        content = MemoryContent(
            title=f"Role performance for {role_id}",
            body=updated.model_dump_json(),
            context=f"role_id={role_id} workspace_id={workspace_id}",
            outcome=(
                "verification=passed"
                if verification_report.passed
                else "verification=failed"
            ),
        )
        if entry_id is None:
            await self._memory_bank.create_entry_async(
                CreateMemoryEntryRequest(
                    tier=MemoryTier.PERSISTENT,
                    scope=MemoryScope.ROLE,
                    workspace_id=workspace_id,
                    role_id=role_id,
                    kind=MemoryEntryKind.INSIGHT,
                    content=content,
                    tags=("role-performance",),
                    source=MemorySourceKind.TASK_RESULT,
                    source_ref=task_id,
                )
            )
            return

        await self._memory_bank.update_entry_async(
            entry_id,
            UpdateMemoryEntryRequest(
                content=content,
                tags=("role-performance",),
            ),
        )

    async def _get_role_performance_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> tuple[str | None, RolePerformanceMetrics | None]:
        result = await self._memory_bank.list_entries_async(
            MemoryQuery(
                workspace_id=workspace_id,
                scope=MemoryScope.ROLE,
                role_id=role_id,
                kind=MemoryEntryKind.INSIGHT,
                status=MemoryEntryStatus.ACTIVE,
                tags=("role-performance",),
                limit=20,
            )
        )
        for summary in result.items:
            entry = await self._memory_bank.get_entry_async(summary.id)
            if entry is None:
                continue
            try:
                return entry.id, RolePerformanceMetrics.model_validate_json(
                    entry.content.body
                )
            except ValueError:
                continue
        return None, None

    async def on_run_completed_async(
        self,
        *,
        workspace_id: str,
        session_id: str,
        role_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Consolidate WORKING entries -> MEDIUM_TERM on run completion.

        Performs structural consolidation and then additionally
        triggers SEMANTIC mode consolidation for high-signal extraction.
        SEMANTIC failures do not affect the structural path.
        """
        # 1. Structural consolidation.
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

    async def on_session_completed_async(
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
            result = await self._memory_bank.consolidate_async(request)
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

    async def get_injectable_memory_text_async(
        self,
        *,
        workspace_id: str,
        role_id: str | None = None,
    ) -> str:
        """Build injectable memory text from PERSISTENT and MEDIUM_TERM entries.

        Used by prompt assembly to include Memory Bank content.
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
                result = await self._memory_bank.list_entries_async(query)
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


def _apply_verification_report(
    *,
    role_id: str,
    workspace_id: str,
    performance: RolePerformanceMetrics | None,
    verification_report: VerificationReport,
) -> RolePerformanceMetrics:
    current = performance or _empty_role_performance(
        role_id=role_id,
        workspace_id=workspace_id,
    )

    total_tasks = current.task_counts.total_tasks + 1
    if verification_report.passed:
        successful_tasks = current.task_counts.successful_tasks + 1
        failed_tasks = current.task_counts.failed_tasks
    else:
        successful_tasks = current.task_counts.successful_tasks
        failed_tasks = current.task_counts.failed_tasks + 1

    total_verifications = current.verification_pass_rate.total_verifications + 1
    passed_verifications = current.verification_pass_rate.passed_verifications
    if verification_report.passed:
        passed_verifications += 1

    pass_rate = passed_verifications / total_verifications
    report_score = _compute_report_score(verification_report)
    old_average = current.average_verification_score
    old_count = current.verification_pass_rate.total_verifications
    new_count = old_count + 1
    average_score = (
        old_average + (report_score - old_average) / new_count
        if new_count > 0
        else report_score
    )

    now = datetime.now(tz=timezone.utc)
    trend = (
        *current.trend,
        PerformanceTrendPoint(
            recorded_at=now,
            verification_pass_rate=pass_rate,
            average_verification_score=round(average_score, 2),
            total_tasks_at_point=total_tasks,
        ),
    )
    if len(trend) > 20:
        trend = trend[-20:]

    return RolePerformanceMetrics(
        role_id=role_id,
        workspace_id=workspace_id,
        verification_pass_rate=VerificationPassRate(
            total_verifications=total_verifications,
            passed_verifications=passed_verifications,
            pass_rate=pass_rate,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=total_tasks,
            successful_tasks=successful_tasks,
            failed_tasks=failed_tasks,
        ),
        average_verification_score=round(average_score, 2),
        trend=trend,
        last_evaluated_at=current.last_evaluated_at,
    )


def _empty_role_performance(
    *,
    role_id: str,
    workspace_id: str,
) -> RolePerformanceMetrics:
    return RolePerformanceMetrics(
        role_id=role_id,
        workspace_id=workspace_id,
        verification_pass_rate=VerificationPassRate(
            total_verifications=0,
            passed_verifications=0,
            pass_rate=0.0,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=0,
            successful_tasks=0,
            failed_tasks=0,
        ),
        average_verification_score=0.0,
        trend=(),
        last_evaluated_at=None,
    )


def _compute_report_score(verification_report: VerificationReport) -> float:
    checks = verification_report.checks
    if not checks:
        return 5.0 if verification_report.passed else 0.0
    passed_count = sum(1 for check in checks if check.passed)
    total_count = len(checks)
    if total_count == 0:
        return 5.0 if verification_report.passed else 0.0
    return (passed_count / total_count) * 5.0
