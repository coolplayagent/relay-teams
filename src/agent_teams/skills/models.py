from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


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
