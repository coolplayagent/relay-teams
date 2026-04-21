# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.validation import RequiredIdentifierStr


class SshProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    username: str | None = None
    password: str | None = Field(default=None, min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    remote_shell: str | None = Field(default=None, min_length=1)
    connect_timeout_seconds: int | None = Field(default=None, ge=1)
    private_key: str | None = Field(default=None, min_length=1)
    private_key_name: str | None = Field(default=None, min_length=1)

    @field_validator("host")
    @classmethod
    def _normalize_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("host must not be empty")
        return normalized

    @field_validator("username", "password", "remote_shell", "private_key_name")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("private_key")
    @classmethod
    def _normalize_private_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return normalized or None


class SshProfileStoredConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    username: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    remote_shell: str | None = Field(default=None, min_length=1)
    connect_timeout_seconds: int | None = Field(default=None, ge=1)
    private_key_name: str | None = Field(default=None, min_length=1)


class SshProfileRecord(SshProfileStoredConfig):
    model_config = ConfigDict(extra="forbid")

    ssh_profile_id: RequiredIdentifierStr
    has_password: bool = False
    has_private_key: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class SshProfilePasswordRevealView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str | None = None


class SshProfileCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class SshProfilePreparedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    argv: tuple[str, ...]
    env: dict[str, str]
    temp_root: Path


class SshProfileConnectivityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssh_profile_id: RequiredIdentifierStr | None = None
    override: SshProfileConfig | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=300000)

    @model_validator(mode="after")
    def _require_profile_or_override(self) -> SshProfileConnectivityProbeRequest:
        if self.ssh_profile_id is None and self.override is None:
            raise ValueError("ssh_profile_id or override is required")
        return self


class SshProfileConnectivityDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binary_available: bool
    host_reachable: bool
    used_password: bool
    used_private_key: bool
    used_system_config: bool
    exit_code: int | None = None


class SshProfileConnectivityProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    ssh_profile_id: str | None = None
    host: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: SshProfileConnectivityDiagnostics
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
