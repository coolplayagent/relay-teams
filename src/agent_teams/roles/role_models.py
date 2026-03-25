# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.roles.memory_models import MemoryProfile, default_memory_profile


class RoleConfigSource(str, Enum):
    BUILTIN = "builtin"
    APP = "app"


class RoleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default")
    bound_agent_id: str | None = None
    memory_profile: MemoryProfile = Field(default_factory=default_memory_profile)
    system_prompt: str = Field(min_length=1)


class RoleDocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    bound_agent_id: str | None = None
    source: RoleConfigSource = RoleConfigSource.APP


class RoleDocumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_role_id: str | None = None
    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default", min_length=1)
    bound_agent_id: str | None = None
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

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class RoleAgentOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)


class RoleConfigOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinator_role_id: str = Field(min_length=1)
    main_agent_role_id: str = Field(min_length=1)
    normal_mode_roles: tuple[NormalModeRoleOption, ...] = ()
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    agents: tuple[RoleAgentOption, ...] = ()
