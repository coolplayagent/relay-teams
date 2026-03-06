# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends

from agent_teams.env.runtime_config import load_runtime_config
from agent_teams.interfaces.server.config_paths import get_config_dir
from agent_teams.interfaces.server.deps import get_role_registry
from agent_teams.roles.registry import RoleLoader, RoleRegistry
from agent_teams.tools.registry import build_default_registry

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.get("")
def list_roles(
    role_registry: RoleRegistry = Depends(get_role_registry),
) -> list[dict[str, object]]:
    return [role.model_dump() for role in role_registry.list_roles()]


@router.post(":validate")
def validate_roles() -> dict[str, int | bool]:
    config = load_runtime_config(config_dir=get_config_dir())
    registry = RoleLoader().load_all(config.paths.roles_dir)
    tool_registry = build_default_registry()

    for role in registry.list_roles():
        tool_registry.validate_known(role.tools)

    return {"valid": True, "loaded_count": len(registry.list_roles())}
