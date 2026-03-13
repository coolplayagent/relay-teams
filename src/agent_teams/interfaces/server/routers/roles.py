# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_teams.interfaces.server.deps import (
    get_mcp_service,
    get_role_registry,
    get_role_settings_service,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.mcp.service import McpService
from agent_teams.roles import (
    RoleConfigOptions,
    RoleDocumentDraft,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleRegistry,
    RoleValidationResult,
)
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.skills.registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry
from agent_teams.workspace import WorkspaceBinding

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.get("")
def list_roles(
    role_registry: RoleRegistry = Depends(get_role_registry),
) -> list[dict[str, object]]:
    return [role.model_dump() for role in role_registry.list_roles()]


@router.get(":options", response_model=RoleConfigOptions)
def get_role_config_options(
    role_registry: RoleRegistry = Depends(get_role_registry),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    mcp_service: McpService = Depends(get_mcp_service),
    skill_registry: SkillRegistry = Depends(get_skill_registry),
) -> RoleConfigOptions:
    return RoleConfigOptions(
        coordinator_role_id=role_registry.get_coordinator_role_id(),
        tools=tool_registry.list_names(),
        mcp_servers=tuple(server.name for server in mcp_service.list_servers()),
        skills=skill_registry.list_names(),
        workspace_bindings=tuple(binding.value for binding in WorkspaceBinding),
    )


@router.get("/configs", response_model=list[RoleDocumentSummary])
def list_role_configs(
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> tuple[RoleDocumentSummary, ...]:
    return service.list_role_documents()


@router.get("/configs/{role_id}", response_model=RoleDocumentRecord)
def get_role_config(
    role_id: str,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleDocumentRecord:
    try:
        return service.get_role_document(role_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/configs/{role_id}", response_model=RoleDocumentRecord)
def save_role_config(
    role_id: str,
    draft: RoleDocumentDraft,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleDocumentRecord:
    try:
        return service.save_role_document(role_id, draft)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(":validate", response_model=dict[str, int | bool])
def validate_roles(
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> dict[str, int | bool]:
    try:
        return service.validate_all_roles()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(":validate-config", response_model=RoleValidationResult)
def validate_role_config(
    draft: RoleDocumentDraft,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleValidationResult:
    try:
        return service.validate_role_document(draft)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
