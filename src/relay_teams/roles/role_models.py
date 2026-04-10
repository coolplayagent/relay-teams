# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from relay_teams.computer import ExecutionSurface
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.roles.memory_models import MemoryProfile, default_memory_profile
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class RoleConfigSource(str, Enum):
    BUILTIN = "builtin"
    APP = "app"


class RoleMode(str, Enum):
    PRIMARY = "primary"
    SUBAGENT = "subagent"
    ALL = "all"


class RoleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default")
    bound_agent_id: OptionalIdentifierStr = None
    execution_surface: ExecutionSurface = ExecutionSurface.API
    mode: RoleMode = RoleMode.PRIMARY
    memory_profile: MemoryProfile = Field(default_factory=default_memory_profile)
    system_prompt: str = Field(min_length=1)


class RoleDocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    bound_agent_id: OptionalIdentifierStr = None
    execution_surface: ExecutionSurface = ExecutionSurface.API
    mode: RoleMode = RoleMode.PRIMARY
    source: RoleConfigSource = RoleConfigSource.APP
    deletable: bool = False


class RoleDocumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_role_id: OptionalIdentifierStr = None
    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default", min_length=1)
    bound_agent_id: OptionalIdentifierStr = None
    execution_surface: ExecutionSurface = ExecutionSurface.API
    mode: RoleMode = RoleMode.PRIMARY
    memory_profile: MemoryProfile = Field(default_factory=default_memory_profile)
    system_prompt: str = Field(min_length=1)


class RoleDocumentRecord(RoleDocumentDraft):
    source: RoleConfigSource = RoleConfigSource.APP
    file_name: str = Field(min_length=1)
    content: str = Field(min_length=1)


class RoleValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    role: RoleDocumentRecord


class NormalModeRoleOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class RoleAgentOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)


class RoleSkillOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    scope: str = Field(pattern="^(builtin|app)$")


class RoleConfigOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinator_role_id: RequiredIdentifierStr
    main_agent_role_id: RequiredIdentifierStr
    normal_mode_roles: tuple[NormalModeRoleOption, ...] = ()
    subagent_roles: tuple[NormalModeRoleOption, ...] = ()
    role_modes: tuple[RoleMode, ...] = Field(default=tuple(mode for mode in RoleMode))
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[RoleSkillOption, ...] = ()
    agents: tuple[RoleAgentOption, ...] = ()
    execution_surfaces: tuple[ExecutionSurface, ...] = Field(
        default=tuple(surface for surface in ExecutionSurface)
    )
