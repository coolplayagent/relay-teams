# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.memory.models import (
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryScope,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_models import RolePerformanceMetrics
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry


def build_role_with_memory(
    *,
    role_registry: RoleRegistry,
    role_memory_service: RoleMemoryService | None,
    role: RoleDefinition,
    role_id: str,
    workspace_id: str,
    memory_bank_service: MemoryBankService | None = None,
) -> RoleDefinition:
    if (
        role_registry.is_coordinator_role(role_id)
        or role.memory_profile.enabled is False
    ):
        return role

    if role_memory_service is None and memory_bank_service is None:
        return role

    sections: list[str] = []

    # Legacy reflection memory section
    if role_memory_service is not None:
        reflection_text = role_memory_service.build_injected_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        if reflection_text:
            sections.append(f"## Reflection Memory\n{reflection_text}")

    # New structured memory bank section (PERSISTENT + MEDIUM_TERM only)
    if memory_bank_service is not None:
        project_memory = _build_project_memory_section(
            memory_bank_service=memory_bank_service,
            workspace_id=workspace_id,
            role_id=role_id,
        )
        if project_memory:
            sections.append(f"## Project Memory\n{project_memory}")

    # RP-2: Role Evolution section (performance + maturity)
    if role_memory_service is not None:
        performance = role_memory_service.get_performance_metrics(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        if performance is not None and performance.task_counts.total_tasks > 0:
            evolution = _build_role_evolution_section(
                memory_bank_service=memory_bank_service,
                workspace_id=workspace_id,
                role_id=role_id,
                performance=performance,
            )
            if evolution:
                sections.append(f"## Role Evolution\n{evolution}")

    if not sections:
        return role

    combined = "\n\n".join(sections)
    return role.model_copy(
        update={
            "system_prompt": f"{role.system_prompt}\n\n{combined}",
        }
    )


def _build_role_evolution_section(
    *,
    memory_bank_service: MemoryBankService | None,
    workspace_id: str,
    role_id: str,
    performance: RolePerformanceMetrics,
) -> str:
    """Build the RP-2 Role Evolution section for prompt injection."""

    pr = performance.verification_pass_rate
    tc = performance.task_counts
    pass_rate_pct = round(pr.pass_rate * 100, 1)

    lines: list[str] = [
        f"- Verification Pass Rate: {pass_rate_pct}% "
        f"({pr.passed_verifications}/{pr.total_verifications} verifications passed)",
        f"- Tasks Completed: {tc.total_tasks} total "
        f"({tc.successful_tasks} successful, {tc.failed_tasks} failed)",
        f"- Average Verification Score: {performance.average_verification_score:.1f}/5.0",
    ]

    # Try to find the latest maturity score from the memory bank
    maturity_level: str | None = None
    adjustment_count: int | None = None
    if memory_bank_service is not None:
        maturity_level = _find_latest_maturity_level(
            memory_bank_service=memory_bank_service,
            workspace_id=workspace_id,
            role_id=role_id,
        )
        adjustment_count = _count_applied_adjustments(
            memory_bank_service=memory_bank_service,
            workspace_id=workspace_id,
            role_id=role_id,
        )

    if maturity_level is not None:
        lines.insert(0, f"- Maturity Level: {maturity_level}")
    if adjustment_count is not None and adjustment_count > 0:
        lines.append(f"- Prompt Adjustments Applied: {adjustment_count}")

    return "\n".join(lines)


def _find_latest_maturity_level(
    *,
    memory_bank_service: MemoryBankService,
    workspace_id: str,
    role_id: str,
) -> str | None:
    """Find the latest maturity level (L1-L5) from memory bank entries."""
    query = MemoryQuery(
        workspace_id=workspace_id,
        scope=MemoryScope.ROLE,
        role_id=role_id,
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        limit=10,
    )
    try:
        result = memory_bank_service.list_entries(query)
    except (ValueError, OSError, RuntimeError):
        return None

    for entry in result.items:
        title = entry.content_title or ""
        if title.startswith("Maturity ") and "- Level: " in title:
            return title.split("- Level: ", 1)[-1].strip()
        if (
            title.startswith("L1")
            or title.startswith("L2")
            or title.startswith("L3")
            or title.startswith("L4")
            or title.startswith("L5")
        ):
            return title.strip()
    return None


def _count_applied_adjustments(
    *,
    memory_bank_service: MemoryBankService,
    workspace_id: str,
    role_id: str,
) -> int | None:
    """Count applied prompt adjustments from memory bank entries."""
    query = MemoryQuery(
        workspace_id=workspace_id,
        scope=MemoryScope.ROLE,
        role_id=role_id,
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        limit=50,
    )
    try:
        result = memory_bank_service.list_entries(query)
    except (ValueError, OSError, RuntimeError):
        return None

    count = 0
    for entry in result.items:
        title = entry.content_title or ""
        if "prompt_applied" in title.lower() or "adjustment applied" in title.lower():
            count += 1
    return count


def _build_project_memory_section(
    *,
    memory_bank_service: MemoryBankService,
    workspace_id: str,
    role_id: str | None = None,
) -> str:
    """Build injectable memory text from PERSISTENT and MEDIUM_TERM entries."""
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
            result = memory_bank_service.list_entries(query)
        except (ValueError, OSError, RuntimeError):
            continue
        if not result.items:
            continue
        tier_label = tier.value.replace("_", " ").title()
        lines.append(f"### {tier_label}")
        for entry in result.items:
            lines.append(f"- [{entry.kind.value}] {entry.content_title}")

    return "\n".join(lines)
