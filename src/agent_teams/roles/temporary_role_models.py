# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.memory_models import MemoryProfile, default_memory_profile


class TemporaryRoleSource(str, Enum):
    META_AGENT_GENERATED = "meta_agent_generated"


class TemporaryRoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1, default="temporary")
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default", min_length=1)
    bound_agent_id: str | None = None
    memory_profile: MemoryProfile = Field(default_factory=default_memory_profile)
    system_prompt: str = Field(min_length=1)
    template_role_id: str | None = None

    def to_role_definition(self) -> RoleDefinition:
        return RoleDefinition(
            role_id=self.role_id,
            name=self.name,
            description=self.description,
            version=self.version,
            tools=self.tools,
            mcp_servers=self.mcp_servers,
            skills=self.skills,
            model_profile=self.model_profile,
            bound_agent_id=self.bound_agent_id,
            memory_profile=self.memory_profile,
            system_prompt=self.system_prompt,
        )


class TemporaryRoleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source: TemporaryRoleSource = TemporaryRoleSource.META_AGENT_GENERATED
    role: TemporaryRoleSpec
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
