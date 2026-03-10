# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.workspace import WorkspaceProfile, default_workspace_profile


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
