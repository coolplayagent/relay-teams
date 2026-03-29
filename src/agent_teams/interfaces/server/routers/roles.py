# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_teams.computer import ExecutionSurface
from agent_teams.interfaces.server.deps import (
    get_external_agent_config_service,
    get_mcp_service,
    get_role_registry,
    get_role_settings_service,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.mcp.mcp_service import McpService
from agent_teams.roles import (
    NormalModeRoleOption,
    RoleAgentOption,
    RoleConfigOptions,
    RoleDocumentDraft,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleRegistry,
    RoleSkillOption,
    RoleValidationResult,
)
from agent_teams.external_agents import ExternalAgentConfigService
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry

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
    external_agent_service: ExternalAgentConfigService = Depends(
        get_external_agent_config_service
    ),
) -> RoleConfigOptions:
    return RoleConfigOptions(
        coordinator_role_id=role_registry.get_coordinator_role_id(),
        main_agent_role_id=role_registry.get_main_agent_role_id(),
        normal_mode_roles=tuple(
            NormalModeRoleOption(
                role_id=role.role_id,
                name=role.name,
                description=role.description,
            )
            for role in role_registry.list_normal_mode_roles()
        ),
        tools=tool_registry.list_configurable_names(),
        mcp_servers=tuple(server.name for server in mcp_service.list_servers()),
        skills=tuple(
            RoleSkillOption(
                ref=skill.ref,
                name=skill.name,
                description=skill.description,
                scope=skill.scope,
            )
            for skill in skill_registry.list_skill_options()
        ),
        agents=tuple(
            RoleAgentOption(
                agent_id=agent.agent_id,
                name=agent.name,
                transport=agent.transport.value,
            )
            for agent in external_agent_service.list_agent_options()
        ),
        execution_surfaces=tuple(surface for surface in ExecutionSurface),
    )


@router.get(
    "/configs",
    response_model=list[RoleDocumentSummary],
    response_model_exclude_none=True,
)
def list_role_configs(
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> tuple[RoleDocumentSummary, ...]:
    return service.list_role_documents()


@router.get(
    "/configs/{role_id}",
    response_model=RoleDocumentRecord,
    response_model_exclude_none=True,
)
def get_role_config(
    role_id: str,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleDocumentRecord:
    try:
        return service.get_role_document(role_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put(
    "/configs/{role_id}",
    response_model=RoleDocumentRecord,
    response_model_exclude_none=True,
)
def save_role_config(
    role_id: str,
    draft: RoleDocumentDraft,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleDocumentRecord:
    try:
        return service.save_role_document(role_id, draft)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/configs/{role_id}")
def delete_role_config(
    role_id: str,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> dict[str, str]:
    try:
        service.delete_role_document(role_id)
        return {"status": "ok"}
    except ValueError as exc:
        if str(exc).startswith("Role not found:"):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(":validate", response_model=dict[str, int | bool])
def validate_roles(
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> dict[str, int | bool]:
    try:
        return service.validate_all_roles()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    ":validate-config",
    response_model=RoleValidationResult,
    response_model_exclude_none=True,
)
def validate_role_config(
    draft: RoleDocumentDraft,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleValidationResult:
    try:
        return service.validate_role_document(draft)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
