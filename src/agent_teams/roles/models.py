# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.workspace import WorkspaceProfile, default_workspace_profile


class RoleConfigSource(str, Enum):
    BUILTIN = "builtin"
    APP = "app"


class RoleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default")
    workspace_profile: WorkspaceProfile = Field(
        default_factory=default_workspace_profile
    )
    system_prompt: str = Field(min_length=1)


class RoleDocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    source: RoleConfigSource = RoleConfigSource.APP


class RoleDocumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_role_id: str | None = None
    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default", min_length=1)
    workspace_profile: WorkspaceProfile = Field(
        default_factory=default_workspace_profile
    )
    system_prompt: str = Field(min_length=1)


class RoleDocumentRecord(RoleDocumentDraft):
    source: RoleConfigSource = RoleConfigSource.APP
    file_name: str = Field(min_length=1)
    content: str = Field(min_length=1)


class RoleValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    role: RoleDocumentRecord


class RoleConfigOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinator_role_id: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    workspace_bindings: tuple[str, ...] = ()
