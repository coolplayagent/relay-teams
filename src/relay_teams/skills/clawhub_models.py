# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.skills.skill_models import SkillScope
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
    scope: SkillScope = SkillScope.APP
    directory: str
    manifest_path: str
    valid: bool = True
    error: str | None = None


class ClawHubSkillDetail(ClawHubSkillSummary):
    model_config = ConfigDict(extra="forbid")

    instructions: str = ""
    manifest_content: str | None = None
    files: tuple[ClawHubSkillFile, ...] = ()


class ClawHubRemoteSkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: RequiredIdentifierStr
    title: str = Field(min_length=1)
    version: str | None = None
    score: float | None = None


class ClawHubSkillSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    token: str | None = None


class ClawHubSkillInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: RequiredIdentifierStr
    version: str | None = None
    force: bool = False
    token: str | None = None


class ClawHubSkillSearchDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binary_available: bool
    token_configured: bool
    installation_attempted: bool = False
    installed_during_search: bool = False
    registry: str | None = None
    endpoint_fallback_used: bool = False


class ClawHubSkillInstallDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binary_available: bool
    token_configured: bool
    installation_attempted: bool = False
    installed_during_install: bool = False
    registry: str | None = None
    endpoint_fallback_used: bool = False
    workdir: str | None = None
    skills_reloaded: bool = False


class ClawHubSkillSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    query: str = Field(min_length=1)
    items: tuple[ClawHubRemoteSkillSummary, ...] = ()
    clawhub_path: str | None = None
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: ClawHubSkillSearchDiagnostics
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None


class ClawHubSkillInstallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    slug: RequiredIdentifierStr
    requested_version: str | None = None
    installed_skill: ClawHubSkillSummary | None = None
    clawhub_path: str | None = None
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: ClawHubSkillInstallDiagnostics
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
