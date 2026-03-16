# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry


def build_role_with_memory(
    *,
    role_registry: RoleRegistry,
    role_memory_service: RoleMemoryService | None,
    role: RoleDefinition,
    role_id: str,
    workspace_id: str,
) -> RoleDefinition:
    if (
        role_registry.is_coordinator_role(role_id)
        or role_memory_service is None
        or role.memory_profile.enabled is False
    ):
        return role

    memory_text = role_memory_service.build_injected_memory(
        role_id=role_id,
        workspace_id=workspace_id,
    )
    if not memory_text:
        return role

    return role.model_copy(
        update={
            "system_prompt": f"{role.system_prompt}\n\n## Reflection Memory\n{memory_text}",
        }
    )
