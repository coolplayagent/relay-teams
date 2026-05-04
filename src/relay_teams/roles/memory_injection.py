# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.memory.service import MemoryBankService
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

    if not sections:
        return role

    combined = "\n\n".join(sections)
    return role.model_copy(
        update={
            "system_prompt": f"{role.system_prompt}\n\n{combined}",
        }
    )


def _build_project_memory_section(
    *,
    memory_bank_service: MemoryBankService,
    workspace_id: str,
    role_id: str | None = None,
) -> str:
    """Build injectable memory text from PERSISTENT and MEDIUM_TERM entries."""
    from relay_teams.memory.models import (
        MemoryEntryStatus,
        MemoryQuery,
        MemoryTier,
    )

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
