# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.validation import RequiredIdentifierStr


class SshProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    username: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    remote_shell: str | None = Field(default=None, min_length=1)
    connect_timeout_seconds: int | None = Field(default=None, ge=1)

    @field_validator("host")
    @classmethod
    def _normalize_host(cls, value: str) -> str:
        return value.strip()

    @field_validator("username", "remote_shell")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SshProfileRecord(SshProfileConfig):
    model_config = ConfigDict(extra="forbid")

    ssh_profile_id: RequiredIdentifierStr
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
