# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS

DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS


class ProviderType(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ECHO = "echo"


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=1)
    top_k: int | None = Field(default=None, ge=1)


class ModelEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    ssl_verify: bool | None = None
    context_window: int | None = Field(default=None, ge=1)
    connect_timeout_seconds: float = Field(
        default=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
        gt=0.0,
        le=300.0,
    )
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)

    @field_validator("model", "base_url", "api_key", mode="before")
    @classmethod
    def _normalize_string_fields(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ProviderModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: str = Field(min_length=1)
    provider: ProviderType
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
