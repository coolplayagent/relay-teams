# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.hooks.hook_models import HooksConfig


class SkillSource(str, Enum):
    BUILTIN = "builtin"
    USER_RELAY_TEAMS = "user_relay_teams"
    USER_AGENTS = "user_agents"
    PROJECT_RELAY_TEAMS = "project_relay_teams"
    PROJECT_AGENTS = "project_agents"


class SkillResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    path: Path | None = None
    content: str | None = None


class SkillScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    path: Path


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    instructions: str
    resources: dict[str, SkillResource] = Field(default_factory=dict)
    scripts: dict[str, SkillScript] = Field(default_factory=dict)
    hooks: HooksConfig = Field(default_factory=HooksConfig)


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    metadata: SkillMetadata
    directory: Path
    source: SkillSource


class SkillSummaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    source: SkillSource


class SkillOptionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    source: SkillSource


class SkillInstructionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
