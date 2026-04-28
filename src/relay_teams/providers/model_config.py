# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    RootModel,
    field_validator,
    model_validator,
)

from relay_teams.media import MediaModality
from relay_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS
from relay_teams.validation import RequiredIdentifierStr

DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS
DEFAULT_LLM_RETRY_MAX_RETRIES = 5
DEFAULT_LLM_RETRY_INITIAL_DELAY_MS = 2000
DEFAULT_LLM_RETRY_BACKOFF_MULTIPLIER = 2.0


class ProviderType(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    BIGMODEL = "bigmodel"
    MINIMAX = "minimax"
    MAAS = "maas"
    CODEAGENT = "codeagent"
    ECHO = "echo"


class CodeAgentAuthMethod(StrEnum):
    SSO = "sso"
    PASSWORD = "password"


class ModelFallbackTrigger(StrEnum):
    RATE_LIMIT_AFTER_RETRIES = "rate_limit_after_retries"


class ModelFallbackStrategy(StrEnum):
    SAME_PROVIDER_THEN_OTHER_PROVIDER = "same_provider_then_other_provider"
    OTHER_PROVIDER_ONLY = "other_provider_only"


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
DEFAULT_CODEAGENT_SSO_BASE_URL = (
    "https://ssoproxysvr.cd-cloud-ssoproxysvr.szv.dragon.tools.huawei.com/ssoproxysvr"
)
DEFAULT_CODEAGENT_BASE_URL = "https://codeagentcli.rnd.huawei.com/codeAgentPro"
DEFAULT_CODEAGENT_CLIENT_ID = "com.huawei.devmind.codebot.apibot"
DEFAULT_CODEAGENT_SCOPE = "1000:1002"
DEFAULT_CODEAGENT_SCOPE_RESOURCE = "devuc"


class CodeAgentAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _secret_config_dir: Path | None = PrivateAttr(default=None)
    _secret_owner_id: str | None = PrivateAttr(default=None)

    auth_method: CodeAgentAuthMethod = CodeAgentAuthMethod.SSO
    client_id: str = Field(default=DEFAULT_CODEAGENT_CLIENT_ID, min_length=1)
    scope: str = Field(default=DEFAULT_CODEAGENT_SCOPE, min_length=1)
    scope_resource: str = Field(
        default=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
        min_length=1,
    )
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    has_password: bool = False
    access_token: str | None = Field(default=None, min_length=1)
    refresh_token: str | None = Field(default=None, min_length=1)
    has_access_token: bool = False
    has_refresh_token: bool = False
    oauth_session_id: str | None = Field(default=None, min_length=1)

    @field_validator(
        "client_id",
        "scope",
        "scope_resource",
        "username",
        "password",
        "access_token",
        "refresh_token",
        "oauth_session_id",
        mode="before",
    )
    @classmethod
    def _normalize_string_fields(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @model_validator(mode="after")
    def _sync_configured_flags(self) -> "CodeAgentAuthConfig":
        self.client_id = DEFAULT_CODEAGENT_CLIENT_ID
        self.scope = DEFAULT_CODEAGENT_SCOPE
        self.scope_resource = DEFAULT_CODEAGENT_SCOPE_RESOURCE
        if self.password is not None:
            self.has_password = True
        if self.access_token is not None:
            self.has_access_token = True
        if self.refresh_token is not None:
            self.has_refresh_token = True
        return self

    def with_secret_owner(
        self,
        *,
        config_dir: Path,
        owner_id: str,
    ) -> "CodeAgentAuthConfig":
        copied = self.model_copy()
        copied._secret_config_dir = config_dir
        copied._secret_owner_id = owner_id
        return copied

    @property
    def secret_config_dir(self) -> Path | None:
        return self._secret_config_dir

    @property
    def secret_owner_id(self) -> str | None:
        return self._secret_owner_id


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


class ModelModalityMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: bool | None = None
    image: bool | None = None
    audio: bool | None = None
    video: bool | None = None
    pdf: bool | None = None

    def supported_media_modalities(self) -> tuple[MediaModality, ...]:
        modalities: list[MediaModality] = []
        if self.image is True:
            modalities.append(MediaModality.IMAGE)
        if self.audio is True:
            modalities.append(MediaModality.AUDIO)
        if self.video is True:
            modalities.append(MediaModality.VIDEO)
        return tuple(modalities)


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: ModelModalityMatrix = Field(default_factory=ModelModalityMatrix)
    output: ModelModalityMatrix = Field(default_factory=ModelModalityMatrix)

    def supported_input_modalities(self) -> tuple[MediaModality, ...]:
        return self.input.supported_media_modalities()


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
    codeagent_auth: CodeAgentAuthConfig | None = None
    ssl_verify: bool | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    context_window: int | None = Field(default=None, ge=1)
    fallback_policy_id: str | None = Field(default=None, min_length=1)
    fallback_priority: int = Field(default=0, ge=0, le=1_000_000)
    catalog_provider_id: str | None = Field(default=None, min_length=1)
    catalog_provider_name: str | None = Field(default=None, min_length=1)
    catalog_model_name: str | None = Field(default=None, min_length=1)
    connect_timeout_seconds: float = Field(
        default=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
        gt=0.0,
        le=300.0,
    )
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)

    @field_validator(
        "model",
        "base_url",
        "api_key",
        "catalog_provider_id",
        "catalog_provider_name",
        "catalog_model_name",
        mode="before",
    )
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
        if self.provider == ProviderType.CODEAGENT:
            self.base_url = DEFAULT_CODEAGENT_BASE_URL
            if self.codeagent_auth is None:
                raise ValueError(
                    "CodeAgent model endpoint config requires codeagent_auth configuration."
                )
            if self.codeagent_auth.auth_method == CodeAgentAuthMethod.PASSWORD:
                if (
                    self.codeagent_auth.username is None
                    or self.codeagent_auth.password is None
                ):
                    raise ValueError(
                        "CodeAgent model endpoint config requires codeagent_auth.username and codeagent_auth.password for password auth."
                    )
                return self
            if (
                self.codeagent_auth.refresh_token is None
                and self.codeagent_auth.oauth_session_id is None
            ):
                raise ValueError(
                    "CodeAgent model endpoint config requires codeagent_auth.refresh_token or oauth_session_id."
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

    @model_validator(mode="after")
    def _sync_capabilities(self) -> "ModelEndpointConfig":
        from relay_teams.providers.model_capabilities import resolve_model_capabilities

        self.capabilities = resolve_model_capabilities(
            provider=self.provider,
            base_url=self.base_url,
            model_name=self.model,
            metadata={"capabilities": self.capabilities.model_dump(mode="json")},
        )
        return self


class ModelProfileConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE
    is_default: Optional[bool] = None
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    headers: tuple[ModelRequestHeader, ...] | None = None
    maas_auth: MaaSAuthConfig | None = None
    codeagent_auth: CodeAgentAuthConfig | None = None
    ssl_verify: bool | None = None
    capabilities: ModelCapabilities | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    context_window: Optional[int] = Field(default=None, ge=1)
    fallback_policy_id: Optional[str] = Field(default=None, min_length=1)
    fallback_priority: int = Field(default=0, ge=0, le=1_000_000)
    catalog_provider_id: Optional[str] = Field(default=None, min_length=1)
    catalog_provider_name: Optional[str] = Field(default=None, min_length=1)
    catalog_model_name: Optional[str] = Field(default=None, min_length=1)
    connect_timeout_seconds: float = Field(
        default=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
        gt=0.0,
        le=300.0,
    )

    @field_validator(
        "model",
        "base_url",
        "api_key",
        "catalog_provider_id",
        "catalog_provider_name",
        "catalog_model_name",
        mode="before",
    )
    @classmethod
    def _normalize_string_fields(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class ModelConfigPayload(
    RootModel[dict[RequiredIdentifierStr, ModelProfileConfigPayload]]
):
    root: dict[RequiredIdentifierStr, ModelProfileConfigPayload]


class ProviderModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(min_length=1)
    provider: ProviderType
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    input_modalities: tuple[MediaModality, ...] = ()

    @model_validator(mode="after")
    def _sync_capabilities(self) -> "ProviderModelInfo":
        from relay_teams.providers.model_capabilities import resolve_model_capabilities

        capabilities = resolve_model_capabilities(
            provider=self.provider,
            base_url=self.base_url,
            model_name=self.model,
            metadata={
                "capabilities": self.capabilities.model_dump(mode="json"),
                "input_modalities": [
                    modality.value for modality in self.input_modalities
                ],
            },
        )
        self.capabilities = capabilities
        self.input_modalities = capabilities.supported_input_modalities()
        return self


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


class ModelFallbackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = ""
    enabled: bool = True
    trigger: ModelFallbackTrigger = ModelFallbackTrigger.RATE_LIMIT_AFTER_RETRIES
    strategy: ModelFallbackStrategy
    max_hops: int = Field(default=3, ge=1, le=10)
    cooldown_seconds: int = Field(default=60, ge=0, le=3600)


class ModelFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: tuple[ModelFallbackPolicy, ...] = ()

    @model_validator(mode="after")
    def _validate_unique_policy_ids(self) -> "ModelFallbackConfig":
        seen_ids: set[str] = set()
        for policy in self.policies:
            normalized_id = policy.policy_id.casefold()
            if normalized_id in seen_ids:
                raise ValueError(
                    f"Duplicate model fallback policy id: {policy.policy_id}"
                )
            seen_ids.add(normalized_id)
        return self

    def get_policy(self, policy_id: Optional[str]) -> Optional[ModelFallbackPolicy]:
        if policy_id is None:
            return None
        normalized_id = policy_id.strip().casefold()
        if not normalized_id:
            return None
        for policy in self.policies:
            if policy.policy_id.casefold() == normalized_id:
                return policy
        return None


def default_model_fallback_config() -> ModelFallbackConfig:
    return ModelFallbackConfig(
        policies=(
            ModelFallbackPolicy(
                policy_id="same_provider_then_other_provider",
                name="Same Provider Then Other Provider",
                description=(
                    "Retry the same provider with higher-priority fallback profiles "
                    "before switching to other providers."
                ),
                strategy=(ModelFallbackStrategy.SAME_PROVIDER_THEN_OTHER_PROVIDER),
                max_hops=3,
                cooldown_seconds=60,
            ),
            ModelFallbackPolicy(
                policy_id="other_provider_only",
                name="Other Provider Only",
                description=(
                    "Skip same-provider alternatives and fail over directly to "
                    "profiles from other providers."
                ),
                strategy=ModelFallbackStrategy.OTHER_PROVIDER_ONLY,
                max_hops=3,
                cooldown_seconds=60,
            ),
        )
    )
