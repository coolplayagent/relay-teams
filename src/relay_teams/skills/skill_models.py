# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.hooks.hook_models import HooksConfig


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


def build_skill_ref(*, scope: SkillScope, name: str) -> str:
    return f"{scope.value}:{name}"


def parse_skill_ref(value: str) -> tuple[SkillScope, str] | None:
    normalized = value.strip()
    if ":" not in normalized:
        return None
    scope_text, name = normalized.split(":", maxsplit=1)
    if not name.strip():
        return None
    try:
        scope = SkillScope(scope_text.strip())
    except ValueError:
        return None
    return scope, name.strip()


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
    scope: SkillScope


class SkillSummaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    scope: SkillScope


class SkillOptionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    scope: SkillScope


class SkillInstructionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
