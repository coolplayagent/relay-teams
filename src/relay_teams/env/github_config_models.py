# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from relay_teams.env.public_webhook_url import normalize_public_base_url


class GitHubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    webhook_base_url: str | None = None

    @field_validator("webhook_base_url")
    @classmethod
    def _normalize_webhook_base_url(cls, value: str | None) -> str | None:
        return normalize_public_base_url(value)


class GitHubConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    webhook_base_url: str | None = None

    @field_validator("webhook_base_url")
    @classmethod
    def _normalize_webhook_base_url(cls, value: str | None) -> str | None:
        return normalize_public_base_url(value)


class GitHubConfigView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_configured: bool = False
    webhook_base_url: str | None = None
