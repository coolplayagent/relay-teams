# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from relay_teams.computer import ExecutionSurface
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.roles.default_role_tools import apply_default_role_tools
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.memory_models import MemoryProfile, default_memory_profile
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class TemporaryRoleSource(str, Enum):
    META_AGENT_GENERATED = "meta_agent_generated"


class TemporaryRoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1, default="temporary")
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default", min_length=1)
    bound_agent_id: OptionalIdentifierStr = None
    execution_surface: ExecutionSurface = ExecutionSurface.API
    memory_profile: MemoryProfile = Field(default_factory=default_memory_profile)
    system_prompt: str = Field(min_length=1)
    template_role_id: OptionalIdentifierStr = None

    def to_role_definition(self) -> RoleDefinition:
        return RoleDefinition(
            role_id=self.role_id,
            name=self.name,
            description=self.description,
            version=self.version,
            tools=apply_default_role_tools(
                role_id=self.role_id,
                role_name=self.name,
                tools=self.tools,
            ),
            mcp_servers=self.mcp_servers,
            skills=self.skills,
            model_profile=self.model_profile,
            bound_agent_id=self.bound_agent_id,
            execution_surface=self.execution_surface,
            memory_profile=self.memory_profile,
            system_prompt=self.system_prompt,
        )


class TemporaryRoleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    source: TemporaryRoleSource = TemporaryRoleSource.META_AGENT_GENERATED
    role: TemporaryRoleSpec
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
