# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.skills.skill_models import SkillSource
from relay_teams.validation import RequiredIdentifierStr

ClawHubFileEncoding = Literal["utf-8", "base64"]


class ClawHubSkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str = ""
    encoding: ClawHubFileEncoding = "utf-8"


class ClawHubSkillWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_name: RequiredIdentifierStr
    description: str = ""
    instructions: str = ""
    files: tuple[ClawHubSkillFile, ...] = ()


class ClawHubSkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: RequiredIdentifierStr
    runtime_name: str | None = None
    description: str = ""
    ref: str | None = None
    source: SkillSource = SkillSource.USER_RELAY_TEAMS
    directory: str
    manifest_path: str
    valid: bool = True
    error: str | None = None


class ClawHubSkillDetail(ClawHubSkillSummary):
    model_config = ConfigDict(extra="forbid")

    instructions: str = ""
    manifest_content: str | None = None
    files: tuple[ClawHubSkillFile, ...] = ()
