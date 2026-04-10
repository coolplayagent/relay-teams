# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS

DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS
DEFAULT_LLM_RETRY_MAX_RETRIES = 5
DEFAULT_LLM_RETRY_INITIAL_DELAY_MS = 2000
DEFAULT_LLM_RETRY_BACKOFF_MULTIPLIER = 2.0


class ProviderType(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    BIGMODEL = "bigmodel"
    MINIMAX = "minimax"
    MAAS = "maas"
    ECHO = "echo"


DEFAULT_MAAS_LOGIN_URL = (
    "http://rnd-idea-api.huawei.com/ideaclientservice/login/v4/secureLogin"
)
DEFAULT_MAAS_BASE_URL = (
    "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/"
)
DEFAULT_MAAS_DISCOVERY_URL = (
    "https://promptcenter.aims.cce.prod.dragon.tools.huawei.com/"
    "PromptCenterService/v1/policy/bundle"
)
DEFAULT_MAAS_DISCOVERY_AREA = "green"
DEFAULT_MAAS_DISCOVERY_PLUGIN_VERSION = "1.0.4"
DEFAULT_MAAS_DISCOVERY_APPLICATION = "RelayAgent"
DEFAULT_MAAS_DISCOVERY_IDE = "RelayAgent"
DEFAULT_MAAS_DISCOVERY_PLUGIN_NAME = "maas_relay"
DEFAULT_MAAS_APP_ID = "RelayTeams"


class MaaSAuthConfig(BaseModel):
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


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    top_k: int | None = Field(default=None, ge=1)


class ModelRequestHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: str | None = None
    secret: bool = False
    configured: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _normalize_value(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _sync_configured_flag(self) -> "ModelRequestHeader":
        if self.value is not None:
            self.configured = True
        return self


class ModelEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    headers: tuple[ModelRequestHeader, ...] = ()
    maas_auth: MaaSAuthConfig | None = None
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
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("headers")
    @classmethod
    def _validate_headers(
        cls,
        value: tuple[ModelRequestHeader, ...],
    ) -> tuple[ModelRequestHeader, ...]:
        seen_names: set[str] = set()
        for entry in value:
            normalized_name = entry.name.casefold()
            if normalized_name in seen_names:
                raise ValueError(f"Duplicate model header name: {entry.name}")
            seen_names.add(normalized_name)
        return value

    @model_validator(mode="after")
    def _require_auth_source(self) -> "ModelEndpointConfig":
        if self.provider == ProviderType.MAAS:
            self.base_url = DEFAULT_MAAS_BASE_URL
            if self.maas_auth is None:
                raise ValueError(
                    "MAAS model endpoint config requires maas_auth configuration."
                )
            if self.maas_auth.password is None:
                raise ValueError(
                    "MAAS model endpoint config requires maas_auth.password."
                )
            return self
        if self.api_key is not None:
            return self
        if any(
            header.configured and header.value is not None for header in self.headers
        ):
            return self
        raise ValueError(
            "Model endpoint config requires api_key or at least one configured header."
        )


class ProviderModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: str = Field(min_length=1)
    provider: ProviderType
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)


class LlmRetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_retries: int = Field(default=DEFAULT_LLM_RETRY_MAX_RETRIES, ge=0, le=10)
    initial_delay_ms: int = Field(
        default=DEFAULT_LLM_RETRY_INITIAL_DELAY_MS,
        ge=0,
        le=300000,
    )
    backoff_multiplier: float = Field(
        default=DEFAULT_LLM_RETRY_BACKOFF_MULTIPLIER,
        ge=1.0,
        le=10.0,
    )
    jitter: bool = False
