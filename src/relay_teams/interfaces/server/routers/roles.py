# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends

from relay_teams.computer import ExecutionSurface
from relay_teams.interfaces.server.deps import (
    get_external_agent_config_service,
    get_mcp_service,
    get_model_config_service,
    get_role_registry,
    get_role_settings_service,
    get_skills_config_reload_service,
    get_skill_registry,
    get_tool_registry,
)
from relay_teams.mcp.mcp_service import McpService
from relay_teams.providers.model_config import ModelCapabilities
from relay_teams.providers.model_config_service import ModelConfigService
from relay_teams.providers.provider_factory import resolve_model_profile_config
from relay_teams.roles import (
    NormalModeRoleOption,
    RoleAgentOption,
    RoleConfigOptions,
    RoleDocumentDraft,
    RoleDocumentRecord,
    RoleDocumentSummary,
    RoleRegistry,
    RoleSkillOption,
    RoleToolGroupOption,
    SystemRolesUnavailableError,
    RoleValidationResult,
    ensure_required_system_roles,
)
from relay_teams.external_agents import ExternalAgentConfigService
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.roles.settings_service import RoleSettingsService
from relay_teams.skills.config_reload_service import SkillsConfigReloadService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry import ToolRegistry, list_default_tool_groups
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.get("")
def list_roles(
    role_registry: RoleRegistry = Depends(get_role_registry),
) -> list[dict[str, object]]:
    return [role.model_dump() for role in role_registry.list_roles()]


@router.get(":options", response_model=RoleConfigOptions)
def get_role_config_options(
    role_registry: RoleRegistry = Depends(get_role_registry),
    model_config_service: ModelConfigService = Depends(get_model_config_service),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    mcp_service: McpService = Depends(get_mcp_service),
    skill_registry: SkillRegistry = Depends(get_skill_registry),
    skills_reload_service: SkillsConfigReloadService = Depends(
        get_skills_config_reload_service
    ),
    external_agent_service: ExternalAgentConfigService = Depends(
        get_external_agent_config_service
    ),
) -> RoleConfigOptions:
    try:
        ensure_required_system_roles(role_registry)
        skill_options = _load_role_skill_options(
            role_registry=role_registry,
            skill_registry=skill_registry,
            skills_reload_service=skills_reload_service,
        )
        normal_mode_roles = tuple(
            _build_role_option(role=role, model_config_service=model_config_service)
            for role in role_registry.list_normal_mode_roles()
        )
        subagent_roles = tuple(
            _build_role_option(role=role, model_config_service=model_config_service)
            for role in role_registry.list_subagent_roles()
        )
        coordinator_role = _build_role_option(
            role=role_registry.get_coordinator(),
            model_config_service=model_config_service,
        )
        main_agent_role = _build_role_option(
            role=role_registry.get_main_agent(),
            model_config_service=model_config_service,
        )
        coordinator_role_id = coordinator_role.role_id
        main_agent_role_id = main_agent_role.role_id
        return RoleConfigOptions(
            coordinator_role_id=coordinator_role_id,
            main_agent_role_id=main_agent_role_id,
            coordinator_role=coordinator_role,
            main_agent_role=main_agent_role,
            normal_mode_roles=normal_mode_roles,
            subagent_roles=subagent_roles,
            tool_groups=tuple(
                RoleToolGroupOption(
                    id=group.group_id,
                    name=group.name,
                    description=group.description,
                    tools=group.tools,
                )
                for group in list_default_tool_groups(tool_registry)
            ),
            tools=tool_registry.list_configurable_names(),
            mcp_servers=tuple(server.name for server in mcp_service.list_servers()),
            skills=skill_options,
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
    except (SystemRolesUnavailableError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((SystemRolesUnavailableError, 503), (ValueError, 503)),
        ) from exc


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
    role_id: RequiredIdentifierStr,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleDocumentRecord:
    try:
        return service.get_role_document(role_id)
    except ValueError as exc:
        raise http_exception_for(exc, mappings=((ValueError, 404),)) from exc


@router.put(
    "/configs/{role_id}",
    response_model=RoleDocumentRecord,
    response_model_exclude_none=True,
)
def save_role_config(
    role_id: RequiredIdentifierStr,
    draft: RoleDocumentDraft,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> RoleDocumentRecord:
    try:
        return service.save_role_document(role_id, draft)
    except ValueError as exc:
        raise http_exception_for(exc, mappings=((ValueError, 400),)) from exc


@router.delete("/configs/{role_id}")
def delete_role_config(
    role_id: RequiredIdentifierStr,
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> dict[str, str]:
    try:
        service.delete_role_document(role_id)
        return {"status": "ok"}
    except ValueError as exc:
        if str(exc).startswith("Role not found:"):
            raise http_exception_for(exc, mappings=((ValueError, 404),)) from exc
        raise http_exception_for(exc, mappings=((ValueError, 400),)) from exc


@router.post(":validate", response_model=dict[str, int | bool])
def validate_roles(
    service: RoleSettingsService = Depends(get_role_settings_service),
) -> dict[str, int | bool]:
    try:
        return service.validate_all_roles()
    except (SystemRolesUnavailableError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((SystemRolesUnavailableError, 503), (ValueError, 400)),
        ) from exc


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
        raise http_exception_for(exc, mappings=((ValueError, 400),)) from exc


def _load_role_skill_options(
    *,
    role_registry: RoleRegistry,
    skill_registry: SkillRegistry,
    skills_reload_service: SkillsConfigReloadService,
) -> tuple[RoleSkillOption, ...]:
    skill_options = tuple(skill_registry.list_skill_options())
    required_builtin_refs = _collect_required_builtin_skill_refs(role_registry)
    available_refs = {skill.ref for skill in skill_options}
    if not required_builtin_refs.issubset(available_refs):
        reloaded_registry = skills_reload_service.reload_skills_config()
        skill_options = tuple(reloaded_registry.list_skill_options())
        available_refs = {skill.ref for skill in skill_options}
        missing_refs = sorted(required_builtin_refs.difference(available_refs))
        if missing_refs:
            raise ValueError(f"Builtin skills are unavailable: {missing_refs}")
    return tuple(
        RoleSkillOption(
            ref=skill.ref,
            name=skill.name,
            description=skill.description,
            scope=skill.scope,
        )
        for skill in skill_options
    )


def _collect_required_builtin_skill_refs(role_registry: RoleRegistry) -> frozenset[str]:
    return frozenset(
        skill_name
        for role in role_registry.list_roles()
        for skill_name in role.skills
        if skill_name.startswith("builtin:")
    )


def _build_role_option(
    *,
    role: RoleDocumentDraft | RoleDocumentRecord | RoleDocumentSummary | object,
    model_config_service: ModelConfigService,
) -> NormalModeRoleOption:
    runtime = model_config_service.runtime
    role_model_profile = str(getattr(role, "model_profile", "default") or "default")
    resolved_profile = resolve_model_profile_config(
        runtime=runtime,
        profile_name=role_model_profile,
    )
    capabilities = ModelCapabilities()
    input_modalities = ()
    if resolved_profile is not None:
        capabilities = resolved_profile.capabilities
        input_modalities = capabilities.supported_input_modalities()
    return NormalModeRoleOption(
        role_id=str(getattr(role, "role_id")),
        name=str(getattr(role, "name")),
        description=str(getattr(role, "description")),
        model_profile=role_model_profile,
        model_name=resolved_profile.model if resolved_profile is not None else "",
        capabilities=capabilities,
        input_modalities=input_modalities,
    )
