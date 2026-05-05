# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.agents.tasks.models import VerificationReport
from relay_teams.roles.memory_models import (
    PerformanceTrendPoint,
    RoleMemoryRecord,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)
from relay_teams.roles.memory_repository import RoleMemoryRepository


class RoleMemoryService:
    def __init__(self, *, repository: RoleMemoryRepository) -> None:
        self._repository = repository

    def build_injected_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        memory_date: str | None = None,
    ) -> str:
        del memory_date
        return self.get_reflection_record(
            role_id=role_id,
            workspace_id=workspace_id,
        ).content_markdown.strip()

    def get_reflection_record(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RoleMemoryRecord:
        return self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def update_reflection_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        content_markdown: str,
    ) -> RoleMemoryRecord:
        self._repository.write_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            content_markdown=content_markdown.strip(),
        )
        return self.get_reflection_record(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def delete_reflection_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> None:
        self._repository.delete_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def build_reflection_preview(
        self,
        *,
        role_id: str,
        workspace_id: str,
        max_chars: int = 180,
    ) -> str:
        text = self.build_injected_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        normalized = " ".join(text.split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + "..."

    async def record_verification_outcome(
        self,
        *,
        role_id: str,
        workspace_id: str,
        verification_report: VerificationReport,
    ) -> RoleMemoryRecord:
        record = await self._repository.read_role_memory_async(
            role_id=role_id,
            workspace_id=workspace_id,
        )

        if record.performance is None:
            performance = RolePerformanceMetrics(
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
        else:
            performance = record.performance

        total_tasks = performance.task_counts.total_tasks + 1
        if verification_report.passed:
            successful_tasks = performance.task_counts.successful_tasks + 1
            failed_tasks = performance.task_counts.failed_tasks
        else:
            successful_tasks = performance.task_counts.successful_tasks
            failed_tasks = performance.task_counts.failed_tasks + 1

        total_verifications = performance.verification_pass_rate.total_verifications + 1
        if verification_report.passed:
            passed_verifications = (
                performance.verification_pass_rate.passed_verifications + 1
            )
        else:
            passed_verifications = (
                performance.verification_pass_rate.passed_verifications
            )

        new_pass_rate = (
            passed_verifications / total_verifications
            if total_verifications > 0
            else 0.0
        )

        report_score = _compute_report_score(verification_report)
        old_avg = performance.average_verification_score
        old_n = performance.verification_pass_rate.total_verifications
        new_n = old_n + 1
        new_avg = (
            old_avg + (report_score - old_avg) / new_n if new_n > 0 else report_score
        )

        now = datetime.now(tz=timezone.utc)
        trend_point = PerformanceTrendPoint(
            recorded_at=now,
            verification_pass_rate=new_pass_rate,
            average_verification_score=round(new_avg, 2),
            total_tasks_at_point=total_tasks,
        )
        trend = list(performance.trend)
        trend.append(trend_point)
        if len(trend) > 20:
            trend = trend[-20:]

        updated_performance = RolePerformanceMetrics(
            role_id=role_id,
            workspace_id=workspace_id,
            verification_pass_rate=VerificationPassRate(
                total_verifications=total_verifications,
                passed_verifications=passed_verifications,
                pass_rate=new_pass_rate,
            ),
            task_counts=RoleTaskCounts(
                total_tasks=total_tasks,
                successful_tasks=successful_tasks,
                failed_tasks=failed_tasks,
            ),
            average_verification_score=round(new_avg, 2),
            trend=tuple(trend),
            last_evaluated_at=performance.last_evaluated_at,
        )

        await self._repository.write_role_memory_async(
            role_id=role_id,
            workspace_id=workspace_id,
            content_markdown=record.content_markdown,
            performance=updated_performance,
        )

        return await self._repository.read_role_memory_async(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def get_performance_metrics(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RolePerformanceMetrics | None:
        record = self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        return record.performance

    async def get_performance_metrics_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RolePerformanceMetrics | None:
        record = await self._repository.read_role_memory_async(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        return record.performance

    def record_task_result(
        self,
        *,
        role_id: str,
        workspace_id: str,
        session_id: str,
        task_id: str,
        objective: str,
        result: str,
        transcript_lines: tuple[str, ...],
        memory_date: str | None = None,
    ) -> None:
        del session_id, task_id, transcript_lines, memory_date
        trimmed_result = result.strip()
        trimmed_objective = objective.strip()
        current = self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        ).content_markdown.strip()
        durable_entry = f"- {trimmed_objective}: {trimmed_result or '(empty)'}"
        if durable_entry in current.splitlines():
            return
        next_text = "\n".join(
            line for line in (current, durable_entry) if line.strip()
        ).strip()
        self._repository.write_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            content_markdown=next_text,
        )


def _compute_report_score(verification_report: VerificationReport) -> float:
    checks = verification_report.checks
    if not checks:
        return 5.0 if verification_report.passed else 0.0
    passed_count = sum(1 for c in checks if c.passed)
    total_count = len(checks)
    if total_count == 0:
        return 5.0 if verification_report.passed else 0.0
    return (passed_count / total_count) * 5.0
