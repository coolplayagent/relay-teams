# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.connector.models import ConnectorStatus


class W3ModelImportFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)
    message: str = Field(min_length=1)


class W3ModelSyncSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_count: int = Field(default=0, ge=0)
    created_count: int = Field(default=0, ge=0)
    skipped_existing_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    created_profiles: tuple[str, ...] = ()
    skipped_models: tuple[str, ...] = ()
    failed_models: tuple[W3ModelImportFailure, ...] = ()
    synced_at: datetime | None = None


class W3ConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=1)
    updated_at: datetime | None = None
    last_sync: W3ModelSyncSummary | None = None
    last_error: str | None = Field(default=None, min_length=1)


class W3ConnectorStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = None
    has_password: bool
    status: ConnectorStatus
    updated_at: datetime | None = None
    last_sync: W3ModelSyncSummary | None = None
    last_error: str | None = None


class W3ConnectorSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1)
    password: str | None = Field(default=None, min_length=1)

    @field_validator("username", "password", mode="before")
    @classmethod
    def _normalize_string_fields(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class W3ConnectorTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)

    @field_validator("username", "password", mode="before")
    @classmethod
    def _normalize_string_fields(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class W3ConnectorSaveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: ConnectorStatus
    message: str = Field(min_length=1)
    username: str | None = None
    has_password: bool
    sync: W3ModelSyncSummary | None = None


class W3ConnectorTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: Literal["valid", "needs_config", "error"]
    message: str = Field(min_length=1)
    username: str | None = None
    has_token: bool = False


class W3ConnectorSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str = Field(min_length=1)
    sync: W3ModelSyncSummary
