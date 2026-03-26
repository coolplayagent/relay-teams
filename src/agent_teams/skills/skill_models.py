# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SkillScope(str, Enum):
    BUILTIN = "builtin"
    APP = "app"


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


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: SkillMetadata
    directory: Path
    scope: SkillScope


class SkillSummaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""


class SkillInstructionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
