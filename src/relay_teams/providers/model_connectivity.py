# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from time import perf_counter
from typing import Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from relay_teams.logger import get_logger
from relay_teams.media import MediaModality
from relay_teams.net.clients import create_sync_http_client
from relay_teams.providers.codeagent_auth import (
    CodeAgentOAuthError,
    build_codeagent_request_headers,
    get_codeagent_oauth_tokens,
    get_codeagent_token_service,
)
from relay_teams.providers.maas_auth import (
    MaaSAuthContext,
    MaaSLoginError,
    get_maas_token_service,
)
from relay_teams.providers.known_model_context_windows import (
    infer_known_context_window,
)
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    DEFAULT_CODEAGENT_BASE_URL,
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAAS_APP_ID,
    DEFAULT_MAAS_DISCOVERY_APPLICATION,
    DEFAULT_MAAS_DISCOVERY_AREA,
    DEFAULT_MAAS_DISCOVERY_IDE,
    DEFAULT_MAAS_DISCOVERY_PLUGIN_NAME,
    DEFAULT_MAAS_DISCOVERY_PLUGIN_VERSION,
    DEFAULT_MAAS_DISCOVERY_URL,
    ModelCapabilities,
    MaaSAuthConfig,
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
    SamplingConfig,
)
from relay_teams.providers.model_capabilities import (
    resolve_model_capabilities,
    resolve_model_input_modalities,
)
from relay_teams.providers.openai_support import build_model_request_headers
from relay_teams.sessions.runs.runtime_config import RuntimeConfig


_INVALID_RESPONSE_PAYLOAD = object()
_EVENT_STREAM_PLAIN_TEXT_SUCCESS = object()
_MAX_PROBE_TIMEOUT_MS = 300_000

LOGGER = get_logger(__name__)


def _uses_openai_compatible_transport(provider: ProviderType) -> bool:
    return provider in (
        ProviderType.OPENAI_COMPATIBLE,
        ProviderType.BIGMODEL,
        ProviderType.MINIMAX,
    )


class ModelConnectivityProbeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType | None = None
    model: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    headers: tuple[ModelRequestHeader, ...] = ()
    maas_auth: MaaSAuthConfig | None = None
    codeagent_auth: CodeAgentAuthConfig | None = None
    ssl_verify: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)


class ModelConnectivityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str | None = Field(default=None, min_length=1)
    override: ModelConnectivityProbeOverride | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=_MAX_PROBE_TIMEOUT_MS)


class ModelDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str | None = Field(default=None, min_length=1)
    override: ModelConnectivityProbeOverride | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=_MAX_PROBE_TIMEOUT_MS)


class ModelConnectivityTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ModelConnectivityDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_reachable: bool
    auth_valid: bool
    rate_limited: bool


class ModelConnectivityProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    provider: ProviderType
    model: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: ModelConnectivityDiagnostics
    token_usage: ModelConnectivityTokenUsage | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class ModelDiscoveryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    context_window: int | None = Field(default=None, ge=1)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    input_modalities: tuple[MediaModality, ...] = ()

    @model_validator(mode="after")
    def _sync_capabilities(self) -> "ModelDiscoveryEntry":
        input_capabilities = self.capabilities.input.model_copy(
            update={
                "image": (
                    True
                    if MediaModality.IMAGE in self.input_modalities
                    else self.capabilities.input.image
                ),
                "audio": (
                    True
                    if MediaModality.AUDIO in self.input_modalities
                    else self.capabilities.input.audio
                ),
                "video": (
                    True
                    if MediaModality.VIDEO in self.input_modalities
                    else self.capabilities.input.video
                ),
                "text": (
                    True
                    if self.capabilities.input.text is None
                    else self.capabilities.input.text
                ),
            }
        )
        output_capabilities = self.capabilities.output.model_copy(
            update={
                "text": (
                    True
                    if self.capabilities.output.text is None
                    else self.capabilities.output.text
                )
            }
        )
        self.capabilities = self.capabilities.model_copy(
            update={
                "input": input_capabilities,
                "output": output_capabilities,
            }
        )
        self.input_modalities = self.capabilities.supported_input_modalities()
        return self


class ModelDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    provider: ProviderType
    base_url: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: ModelConnectivityDiagnostics
    models: tuple[str, ...] = ()
    model_entries: tuple[ModelDiscoveryEntry, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class ModelDiscoveryResolvedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType
    base_url: str = Field(min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    headers: tuple[ModelRequestHeader, ...] = ()
    maas_auth: MaaSAuthConfig | None = None
    codeagent_auth: CodeAgentAuthConfig | None = None
    ssl_verify: bool | None = None
    connect_timeout_seconds: float = Field(gt=0.0, le=300.0)


class CodeAgentAuthVerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "reauth_required", "error"]
    checked_at: datetime
    detail: str | None = None


class ModelConnectivityProbeService:
    def __init__(
        self,
        *,
        get_runtime: Callable[[], RuntimeConfig],
    ) -> None:
        self._get_runtime: Callable[[], RuntimeConfig] = get_runtime

    def probe(
        self,
        request: ModelConnectivityProbeRequest,
    ) -> ModelConnectivityProbeResult:
        resolved_config = self._resolve_endpoint_config(request)
        timeout_ms = self._resolve_timeout_ms(request=request, config=resolved_config)
        if resolved_config.provider == ProviderType.ECHO:
            return self._build_echo_result(resolved_config)
        if resolved_config.provider == ProviderType.MAAS:
            return self._probe_maas(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        if resolved_config.provider == ProviderType.CODEAGENT:
            return self._probe_codeagent(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        if _uses_openai_compatible_transport(resolved_config.provider):
            return self._probe_openai_compatible(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        raise ValueError(
            f"Connectivity probe is not supported for provider '{resolved_config.provider.value}'."
        )

    def discover_models(
        self,
        request: ModelDiscoveryRequest,
    ) -> ModelDiscoveryResult:
        resolved_config = self._resolve_model_discovery_config(request)
        timeout_ms = self._resolve_model_discovery_timeout_ms(
            request=request,
            config=resolved_config,
        )
        if resolved_config.provider == ProviderType.ECHO:
            return ModelDiscoveryResult(
                ok=True,
                provider=resolved_config.provider,
                base_url=resolved_config.base_url,
                latency_ms=0,
                checked_at=datetime.now(timezone.utc),
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                models=("echo",),
            )
        if resolved_config.provider == ProviderType.MAAS:
            return self._discover_maas_models(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        if resolved_config.provider == ProviderType.CODEAGENT:
            return self._discover_codeagent_models(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        if _uses_openai_compatible_transport(resolved_config.provider):
            return self._discover_openai_compatible_models(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        raise ValueError(
            f"Model discovery is not supported for provider '{resolved_config.provider.value}'."
        )

    def verify_codeagent_auth(
        self,
        *,
        profile_name: str,
    ) -> CodeAgentAuthVerifyResult:
        runtime = self._get_runtime()
        resolved_profile_name = _resolve_runtime_profile_name(
            runtime=runtime,
            requested_profile_name=profile_name,
        )
        if resolved_profile_name is None:
            raise ValueError(
                f"Model profile '{profile_name}' was not found in runtime config."
            )
        config = runtime.llm_profiles.get(resolved_profile_name)
        if config is None:
            raise ValueError(
                f"Model profile '{profile_name}' was not found in runtime config."
            )
        if config.provider != ProviderType.CODEAGENT:
            raise ValueError(
                f"Model profile '{profile_name}' is not a CodeAgent profile."
            )
        if config.codeagent_auth is None:
            raise ValueError(
                f"Model profile '{profile_name}' does not have CodeAgent auth configured."
            )
        checked_at = datetime.now(timezone.utc)
        return self._verify_codeagent_auth_config(
            config=config,
            checked_at=checked_at,
        )

    def _verify_codeagent_auth_config(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
    ) -> CodeAgentAuthVerifyResult:
        token_or_result = self._get_codeagent_token_for_verify(
            config=config,
            checked_at=checked_at,
        )
        if isinstance(token_or_result, CodeAgentAuthVerifyResult):
            return token_or_result
        response_or_result = self._send_codeagent_auth_verify_request(
            config=config,
            checked_at=checked_at,
            token=token_or_result,
        )
        if isinstance(response_or_result, CodeAgentAuthVerifyResult):
            return response_or_result
        response = response_or_result
        if 200 <= response.status_code < 300:
            return CodeAgentAuthVerifyResult(
                status="valid",
                checked_at=checked_at,
            )
        if response.status_code not in {401, 403}:
            return self._build_codeagent_auth_verify_http_error_result(
                checked_at=checked_at,
                response=response,
            )
        retry_token_or_result = self._get_codeagent_token_for_verify(
            config=config,
            checked_at=checked_at,
            force_refresh=True,
        )
        if isinstance(retry_token_or_result, CodeAgentAuthVerifyResult):
            return retry_token_or_result
        retry_response_or_result = self._send_codeagent_auth_verify_request(
            config=config,
            checked_at=checked_at,
            token=retry_token_or_result,
        )
        if isinstance(retry_response_or_result, CodeAgentAuthVerifyResult):
            return retry_response_or_result
        retry_response = retry_response_or_result
        if 200 <= retry_response.status_code < 300:
            return CodeAgentAuthVerifyResult(
                status="valid",
                checked_at=checked_at,
            )
        if retry_response.status_code in {401, 403}:
            return CodeAgentAuthVerifyResult(
                status="reauth_required",
                checked_at=checked_at,
                detail=self._extract_codeagent_auth_verify_error_message(retry_response)
                or "CodeAgent authentication is no longer valid.",
            )
        return self._build_codeagent_auth_verify_http_error_result(
            checked_at=checked_at,
            response=retry_response,
        )

    def _resolve_endpoint_config(
        self,
        request: ModelConnectivityProbeRequest,
    ) -> ModelEndpointConfig:
        base_config: ModelEndpointConfig | None = None
        if request.profile_name is not None:
            runtime = self._get_runtime()
            resolved_profile_name = _resolve_runtime_profile_name(
                runtime=runtime,
                requested_profile_name=request.profile_name,
            )
            base_config = (
                runtime.llm_profiles.get(resolved_profile_name)
                if resolved_profile_name is not None
                else None
            )
            if base_config is None:
                raise ValueError(
                    f"Model profile '{request.profile_name}' was not found in runtime config."
                )

        if base_config is None and request.override is None:
            raise ValueError("Provide profile_name, override, or both.")

        if base_config is None:
            override = request.override
            if override is None:
                raise ValueError(
                    "Override config is required when profile_name is omitted."
                )
            override_provider = override.provider or ProviderType.OPENAI_COMPATIBLE
            missing_fields: list[str] = []
            if override.model is None:
                missing_fields.append("model")
            if (
                override.base_url is None
                and override_provider != ProviderType.CODEAGENT
            ):
                missing_fields.append("base_url")
            if override_provider == ProviderType.MAAS:
                if override.maas_auth is None:
                    missing_fields.append("maas_auth")
            elif override_provider == ProviderType.CODEAGENT:
                if override.codeagent_auth is None:
                    missing_fields.append("codeagent_auth")
            elif override.api_key is None and not override.headers:
                missing_fields.append("api_key or headers")
            if missing_fields:
                joined_fields = ", ".join(missing_fields)
                raise ValueError(
                    f"Override config is missing required fields: {joined_fields}."
                )
            override_model = cast(str, override.model)
            override_base_url = (
                DEFAULT_CODEAGENT_BASE_URL
                if override_provider == ProviderType.CODEAGENT
                else cast(str, override.base_url)
            )
            return ModelEndpointConfig(
                provider=override_provider,
                model=override_model,
                base_url=override_base_url,
                api_key=override.api_key,
                headers=override.headers,
                maas_auth=override.maas_auth,
                codeagent_auth=override.codeagent_auth,
                ssl_verify=override.ssl_verify,
                capabilities=resolve_model_capabilities(
                    provider=override_provider,
                    base_url=override_base_url,
                    model_name=override_model,
                    metadata=(
                        override.model_dump(mode="json", exclude_none=True)
                        if isinstance(override, BaseModel)
                        else None
                    ),
                ),
                sampling=SamplingConfig(
                    temperature=(
                        override.temperature
                        if override.temperature is not None
                        else 0.2
                    ),
                    top_p=override.top_p if override.top_p is not None else 1.0,
                    max_tokens=override.max_tokens,
                ),
            )

        return self._merge_config(base_config=base_config, override=request.override)

    def _resolve_model_discovery_config(
        self,
        request: ModelDiscoveryRequest,
    ) -> ModelDiscoveryResolvedConfig:
        base_config: ModelEndpointConfig | None = None
        if request.profile_name is not None:
            runtime = self._get_runtime()
            resolved_profile_name = _resolve_runtime_profile_name(
                runtime=runtime,
                requested_profile_name=request.profile_name,
            )
            base_config = (
                runtime.llm_profiles.get(resolved_profile_name)
                if resolved_profile_name is not None
                else None
            )
            if base_config is None:
                raise ValueError(
                    f"Model profile '{request.profile_name}' was not found in runtime config."
                )

        if base_config is None and request.override is None:
            raise ValueError("Provide profile_name, override, or both.")

        override = request.override
        if base_config is None:
            if override is None:
                raise ValueError(
                    "Override config is required when profile_name is omitted."
                )
            override_provider = override.provider or ProviderType.OPENAI_COMPATIBLE
            missing_fields: list[str] = []
            if (
                override.base_url is None
                and override_provider != ProviderType.CODEAGENT
            ):
                missing_fields.append("base_url")
            if override_provider == ProviderType.MAAS:
                if override.maas_auth is None:
                    missing_fields.append("maas_auth")
            elif override_provider == ProviderType.CODEAGENT:
                if override.codeagent_auth is None:
                    missing_fields.append("codeagent_auth")
            elif override.api_key is None and not override.headers:
                missing_fields.append("api_key or headers")
            if missing_fields:
                joined_fields = ", ".join(missing_fields)
                raise ValueError(
                    f"Override config is missing required fields: {joined_fields}."
                )
            return ModelDiscoveryResolvedConfig(
                provider=override_provider,
                base_url=(
                    DEFAULT_CODEAGENT_BASE_URL
                    if override_provider == ProviderType.CODEAGENT
                    else cast(str, override.base_url)
                ),
                api_key=override.api_key,
                headers=override.headers,
                maas_auth=override.maas_auth,
                codeagent_auth=override.codeagent_auth,
                ssl_verify=override.ssl_verify,
                connect_timeout_seconds=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
            )

        resolved_override = override or ModelConnectivityProbeOverride()
        resolved_provider = resolved_override.provider or base_config.provider
        return ModelDiscoveryResolvedConfig(
            provider=resolved_provider,
            base_url=(
                DEFAULT_CODEAGENT_BASE_URL
                if resolved_provider == ProviderType.CODEAGENT
                else resolved_override.base_url or base_config.base_url
            ),
            api_key=resolved_override.api_key or base_config.api_key,
            headers=resolved_override.headers or base_config.headers,
            maas_auth=self._merge_maas_auth(
                base_maas_auth=base_config.maas_auth,
                override_maas_auth=resolved_override.maas_auth,
            ),
            codeagent_auth=self._merge_codeagent_auth(
                base_codeagent_auth=base_config.codeagent_auth,
                override_codeagent_auth=resolved_override.codeagent_auth,
            ),
            ssl_verify=(
                resolved_override.ssl_verify
                if resolved_override.ssl_verify is not None
                else base_config.ssl_verify
            ),
            connect_timeout_seconds=base_config.connect_timeout_seconds,
        )

    def _merge_config(
        self,
        *,
        base_config: ModelEndpointConfig,
        override: ModelConnectivityProbeOverride | None,
    ) -> ModelEndpointConfig:
        if override is None:
            return base_config
        resolved_provider = override.provider or base_config.provider
        resolved_model = override.model or base_config.model
        resolved_base_url = (
            DEFAULT_CODEAGENT_BASE_URL
            if resolved_provider == ProviderType.CODEAGENT
            else override.base_url or base_config.base_url
        )
        return ModelEndpointConfig(
            provider=resolved_provider,
            model=resolved_model,
            base_url=resolved_base_url,
            api_key=override.api_key or base_config.api_key,
            headers=override.headers or base_config.headers,
            maas_auth=self._merge_maas_auth(
                base_maas_auth=base_config.maas_auth,
                override_maas_auth=override.maas_auth,
            ),
            codeagent_auth=self._merge_codeagent_auth(
                base_codeagent_auth=base_config.codeagent_auth,
                override_codeagent_auth=override.codeagent_auth,
            ),
            ssl_verify=(
                override.ssl_verify
                if override.ssl_verify is not None
                else base_config.ssl_verify
            ),
            capabilities=(
                base_config.capabilities
                if (
                    resolved_provider == base_config.provider
                    and resolved_model == base_config.model
                    and resolved_base_url == base_config.base_url
                )
                else resolve_model_capabilities(
                    provider=resolved_provider,
                    base_url=resolved_base_url,
                    model_name=resolved_model,
                )
            ),
            sampling=SamplingConfig(
                temperature=(
                    override.temperature
                    if override.temperature is not None
                    else base_config.sampling.temperature
                ),
                top_p=(
                    override.top_p
                    if override.top_p is not None
                    else base_config.sampling.top_p
                ),
                max_tokens=(
                    override.max_tokens
                    if override.max_tokens is not None
                    else base_config.sampling.max_tokens
                ),
                top_k=base_config.sampling.top_k,
            ),
        )

    def _merge_maas_auth(
        self,
        *,
        base_maas_auth: MaaSAuthConfig | None,
        override_maas_auth: MaaSAuthConfig | None,
    ) -> MaaSAuthConfig | None:
        if override_maas_auth is None:
            return base_maas_auth
        if base_maas_auth is None:
            return override_maas_auth
        return MaaSAuthConfig(
            username=override_maas_auth.username or base_maas_auth.username,
            password=(
                override_maas_auth.password
                if override_maas_auth.password is not None
                else base_maas_auth.password
            ),
        )

    def _merge_codeagent_auth(
        self,
        *,
        base_codeagent_auth: CodeAgentAuthConfig | None,
        override_codeagent_auth: CodeAgentAuthConfig | None,
    ) -> CodeAgentAuthConfig | None:
        if override_codeagent_auth is None:
            return base_codeagent_auth
        if base_codeagent_auth is None:
            return override_codeagent_auth
        if override_codeagent_auth.oauth_session_id is not None:
            merged_auth = CodeAgentAuthConfig(
                client_id=override_codeagent_auth.client_id
                or base_codeagent_auth.client_id,
                scope=override_codeagent_auth.scope or base_codeagent_auth.scope,
                scope_resource=override_codeagent_auth.scope_resource
                or base_codeagent_auth.scope_resource,
                access_token=override_codeagent_auth.access_token,
                refresh_token=override_codeagent_auth.refresh_token,
                has_access_token=override_codeagent_auth.has_access_token,
                has_refresh_token=override_codeagent_auth.has_refresh_token,
                oauth_session_id=override_codeagent_auth.oauth_session_id,
            )
            return self._with_codeagent_secret_owner(
                merged_auth,
                preferred=override_codeagent_auth,
                fallback=base_codeagent_auth,
            )
        merged_auth = CodeAgentAuthConfig(
            client_id=override_codeagent_auth.client_id
            or base_codeagent_auth.client_id,
            scope=override_codeagent_auth.scope or base_codeagent_auth.scope,
            scope_resource=override_codeagent_auth.scope_resource
            or base_codeagent_auth.scope_resource,
            access_token=override_codeagent_auth.access_token
            or base_codeagent_auth.access_token,
            refresh_token=override_codeagent_auth.refresh_token
            or base_codeagent_auth.refresh_token,
            has_access_token=(
                override_codeagent_auth.has_access_token
                or base_codeagent_auth.has_access_token
            ),
            has_refresh_token=(
                override_codeagent_auth.has_refresh_token
                or base_codeagent_auth.has_refresh_token
            ),
            oauth_session_id=override_codeagent_auth.oauth_session_id
            or base_codeagent_auth.oauth_session_id,
        )
        return self._with_codeagent_secret_owner(
            merged_auth,
            preferred=override_codeagent_auth,
            fallback=base_codeagent_auth,
        )

    def _with_codeagent_secret_owner(
        self,
        auth_config: CodeAgentAuthConfig,
        *,
        preferred: CodeAgentAuthConfig,
        fallback: CodeAgentAuthConfig,
    ) -> CodeAgentAuthConfig:
        config_dir = preferred.secret_config_dir or fallback.secret_config_dir
        owner_id = preferred.secret_owner_id or fallback.secret_owner_id
        if config_dir is None or owner_id is None:
            return auth_config
        return auth_config.with_secret_owner(
            config_dir=config_dir,
            owner_id=owner_id,
        )

    def _resolve_timeout_ms(
        self,
        *,
        request: ModelConnectivityProbeRequest,
        config: ModelEndpointConfig,
    ) -> int:
        if request.timeout_ms is not None:
            return request.timeout_ms
        return int(config.connect_timeout_seconds * 1000)

    def _resolve_model_discovery_timeout_ms(
        self,
        *,
        request: ModelDiscoveryRequest,
        config: ModelDiscoveryResolvedConfig,
    ) -> int:
        if request.timeout_ms is not None:
            return request.timeout_ms
        return int(config.connect_timeout_seconds * 1000)

    def _build_echo_result(
        self,
        config: ModelEndpointConfig,
    ) -> ModelConnectivityProbeResult:
        return ModelConnectivityProbeResult(
            ok=True,
            provider=config.provider,
            model=config.model,
            latency_ms=0,
            checked_at=datetime.now(timezone.utc),
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=True,
                rate_limited=False,
            ),
            token_usage=ModelConnectivityTokenUsage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            ),
        )

    def _probe_maas(
        self,
        *,
        config: ModelEndpointConfig,
        timeout_ms: int,
    ) -> ModelConnectivityProbeResult:
        if config.maas_auth is None:
            raise ValueError("MAAS probe requires maas_auth configuration.")
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = build_model_request_headers(
            config,
            extra_headers={"Content-Type": "application/json"},
        )
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": "reply with pong"}],
            "temperature": config.sampling.temperature,
            "top_p": config.sampling.top_p,
            "max_tokens": 1,
        }
        started = perf_counter()
        checked_at = datetime.now(timezone.utc)
        try:
            token = get_maas_token_service().get_token_sync(
                auth_config=config.maas_auth,
                ssl_verify=config.ssl_verify,
                connect_timeout_seconds=timeout_ms / 1000,
            )
        except httpx.TimeoutException as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )
        except MaaSLoginError as exc:
            return self._build_maas_login_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error=exc,
            )

        headers["X-Auth-Token"] = token
        headers["app-id"] = DEFAULT_MAAS_APP_ID
        response = self._post_probe_request(
            config=config,
            endpoint=endpoint,
            headers=headers,
            payload=payload,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(response, ModelConnectivityProbeResult):
            return response

        if response.status_code in {401, 403}:
            try:
                refreshed_token = get_maas_token_service().get_token_sync(
                    auth_config=config.maas_auth,
                    ssl_verify=config.ssl_verify,
                    connect_timeout_seconds=timeout_ms / 1000,
                    force_refresh=True,
                )
            except httpx.TimeoutException as exc:
                return self._build_transport_error_result(
                    config=config,
                    checked_at=checked_at,
                    started=started,
                    error_code="network_timeout",
                    error_message=str(exc) or "Connection timed out.",
                )
            except httpx.RequestError as exc:
                return self._build_transport_error_result(
                    config=config,
                    checked_at=checked_at,
                    started=started,
                    error_code="network_error",
                    error_message=str(exc) or "Failed to reach model endpoint.",
                )
            except MaaSLoginError as exc:
                return self._build_maas_login_error_result(
                    config=config,
                    checked_at=checked_at,
                    started=started,
                    error=exc,
                )
            retry_headers = dict(headers)
            retry_headers["X-Auth-Token"] = refreshed_token
            response = self._post_probe_request(
                config=config,
                endpoint=endpoint,
                headers=retry_headers,
                payload=payload,
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
            )
            if isinstance(response, ModelConnectivityProbeResult):
                return response

        return self._build_probe_result_from_response(
            config=config,
            response=response,
            checked_at=checked_at,
            started=started,
        )

    def _probe_openai_compatible(
        self,
        *,
        config: ModelEndpointConfig,
        timeout_ms: int,
    ) -> ModelConnectivityProbeResult:
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = build_model_request_headers(
            config,
            extra_headers={"Content-Type": "application/json"},
        )
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": "reply with pong"}],
            "temperature": config.sampling.temperature,
            "top_p": config.sampling.top_p,
            "max_tokens": 1,
        }
        started = perf_counter()
        checked_at = datetime.now(timezone.utc)
        try:
            with create_sync_http_client(
                timeout_seconds=timeout_ms / 1000,
                connect_timeout_seconds=timeout_ms / 1000,
                ssl_verify=config.ssl_verify,
            ) as client:
                response = client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )

        return self._build_probe_result_from_response(
            config=config,
            response=response,
            checked_at=checked_at,
            started=started,
        )

    def _probe_codeagent(
        self,
        *,
        config: ModelEndpointConfig,
        timeout_ms: int,
    ) -> ModelConnectivityProbeResult:
        if config.codeagent_auth is None:
            raise ValueError("CodeAgent probe requires codeagent_auth configuration.")
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": "reply with pong"}],
            "temperature": config.sampling.temperature,
            "top_p": config.sampling.top_p,
            "stream": True,
            "max_tokens": 1,
        }
        started = perf_counter()
        checked_at = datetime.now(timezone.utc)
        token_or_result = self._get_codeagent_token_for_probe(
            config=config,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(token_or_result, ModelConnectivityProbeResult):
            return token_or_result
        headers = build_codeagent_request_headers(
            token=token_or_result,
            content_type="application/json",
            accept="text/event-stream",
        )
        response = self._post_probe_request(
            config=config,
            endpoint=endpoint,
            headers=headers,
            payload=payload,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(response, ModelConnectivityProbeResult):
            return response
        if response.status_code in {401, 403}:
            retry_token_or_result = self._get_codeagent_token_for_probe(
                config=config,
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
                force_refresh=True,
            )
            if isinstance(retry_token_or_result, ModelConnectivityProbeResult):
                return retry_token_or_result
            retry_headers = build_codeagent_request_headers(
                token=retry_token_or_result,
                content_type="application/json",
                accept="text/event-stream",
            )
            response = self._post_probe_request(
                config=config,
                endpoint=endpoint,
                headers=retry_headers,
                payload=payload,
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
            )
            if isinstance(response, ModelConnectivityProbeResult):
                return response
        return self._build_probe_result_from_response(
            config=config,
            response=response,
            checked_at=checked_at,
            started=started,
        )

    def _discover_maas_models(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        timeout_ms: int,
    ) -> ModelDiscoveryResult:
        if config.maas_auth is None:
            raise ValueError("MAAS model discovery requires maas_auth configuration.")
        started = perf_counter()
        checked_at = datetime.now(timezone.utc)
        auth_context_or_result = self._get_maas_model_discovery_auth_context(
            config=config,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(auth_context_or_result, ModelDiscoveryResult):
            return auth_context_or_result
        auth_context = auth_context_or_result

        department = auth_context.department
        assert department is not None
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": auth_context.token,
        }
        payload = self._build_maas_model_discovery_payload(department=department)
        response = self._post_model_discovery_request(
            config=config,
            endpoint=DEFAULT_MAAS_DISCOVERY_URL,
            headers=headers,
            payload=payload,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(response, ModelDiscoveryResult):
            return response

        if response.status_code in {401, 403}:
            refreshed_auth_context_or_result = (
                self._get_maas_model_discovery_auth_context(
                    config=config,
                    checked_at=checked_at,
                    started=started,
                    timeout_ms=timeout_ms,
                    force_refresh=True,
                )
            )
            if isinstance(refreshed_auth_context_or_result, ModelDiscoveryResult):
                return refreshed_auth_context_or_result
            refreshed_auth_context = refreshed_auth_context_or_result

            refreshed_department = refreshed_auth_context.department
            assert refreshed_department is not None
            retry_headers = dict(headers)
            retry_headers["X-Auth-Token"] = refreshed_auth_context.token
            retry_payload = self._build_maas_model_discovery_payload(
                department=refreshed_department
            )
            response = self._post_model_discovery_request(
                config=config,
                endpoint=DEFAULT_MAAS_DISCOVERY_URL,
                headers=retry_headers,
                payload=retry_payload,
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
            )
            if isinstance(response, ModelDiscoveryResult):
                return response

        return self._build_model_discovery_result_from_response(
            config=config,
            response=response,
            checked_at=checked_at,
            started=started,
        )

    def _discover_openai_compatible_models(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        timeout_ms: int,
    ) -> ModelDiscoveryResult:
        endpoint = f"{config.base_url.rstrip('/')}/models"
        headers = build_model_request_headers(
            ModelEndpointConfig(
                provider=config.provider,
                model="discovery",
                base_url=config.base_url,
                api_key=config.api_key,
                headers=config.headers,
                ssl_verify=config.ssl_verify,
            ),
            extra_headers={"Content-Type": "application/json"},
        )
        started = perf_counter()
        checked_at = datetime.now(timezone.utc)
        try:
            with create_sync_http_client(
                timeout_seconds=timeout_ms / 1000,
                connect_timeout_seconds=timeout_ms / 1000,
                ssl_verify=config.ssl_verify,
            ) as client:
                response = client.get(
                    endpoint,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )

        latency_ms = self._latency_ms(started)
        response_payload = self._response_payload(response)
        if response.status_code >= 400:
            error_message = (
                self._extract_error_message(response_payload) or response.text
            )
            return self._build_model_discovery_http_error_result(
                config=config,
                checked_at=checked_at,
                latency_ms=latency_ms,
                status_code=response.status_code,
                error_message=error_message or "Model discovery failed.",
            )

        if response_payload is _INVALID_RESPONSE_PAYLOAD:
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned invalid JSON.",
                retryable=False,
            )

        if not isinstance(response_payload, dict | list):
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned a non-object JSON payload.",
                retryable=False,
            )

        model_entries = self._extract_model_entries(
            payload=response_payload,
            provider=config.provider,
        )
        if model_entries is None:
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned an invalid model catalog payload.",
                retryable=False,
            )

        return ModelDiscoveryResult(
            ok=True,
            provider=config.provider,
            base_url=config.base_url,
            latency_ms=latency_ms,
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=True,
                rate_limited=False,
            ),
            models=tuple(entry.model for entry in model_entries),
            model_entries=model_entries,
        )

    def _discover_codeagent_models(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        timeout_ms: int,
    ) -> ModelDiscoveryResult:
        if config.codeagent_auth is None:
            raise ValueError(
                "CodeAgent model discovery requires codeagent_auth configuration."
            )
        endpoint = f"{config.base_url.rstrip('/')}/chat/modles?checkUserPermission=TRUE"
        started = perf_counter()
        checked_at = datetime.now(timezone.utc)
        token_or_result = self._get_codeagent_token_for_discovery(
            config=config,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(token_or_result, ModelDiscoveryResult):
            return token_or_result
        headers = build_codeagent_request_headers(token=token_or_result)
        response = self._get_model_discovery_request(
            config=config,
            endpoint=endpoint,
            headers=headers,
            checked_at=checked_at,
            started=started,
            timeout_ms=timeout_ms,
        )
        if isinstance(response, ModelDiscoveryResult):
            return response
        if response.status_code in {401, 403}:
            retry_token_or_result = self._get_codeagent_token_for_discovery(
                config=config,
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
                force_refresh=True,
            )
            if isinstance(retry_token_or_result, ModelDiscoveryResult):
                return retry_token_or_result
            response = self._get_model_discovery_request(
                config=config,
                endpoint=endpoint,
                headers=build_codeagent_request_headers(token=retry_token_or_result),
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
            )
            if isinstance(response, ModelDiscoveryResult):
                return response
        return self._build_model_discovery_result_from_response(
            config=config,
            response=response,
            checked_at=checked_at,
            started=started,
        )

    def _get_maas_model_discovery_auth_context(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        started: float,
        timeout_ms: int,
        force_refresh: bool = False,
    ) -> MaaSAuthContext | ModelDiscoveryResult:
        auth_config = cast(MaaSAuthConfig, config.maas_auth)
        try:
            auth_context = get_maas_token_service().get_auth_context_sync(
                auth_config=auth_config,
                ssl_verify=config.ssl_verify,
                connect_timeout_seconds=timeout_ms / 1000,
                force_refresh=force_refresh,
            )
        except httpx.TimeoutException as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )
        except MaaSLoginError as exc:
            return self._build_model_discovery_maas_login_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error=exc,
            )

        if auth_context.department is not None:
            return auth_context
        if not force_refresh:
            return self._get_maas_model_discovery_auth_context(
                config=config,
                checked_at=checked_at,
                started=started,
                timeout_ms=timeout_ms,
                force_refresh=True,
            )
        return self._build_model_discovery_missing_maas_department_result(
            config=config,
            checked_at=checked_at,
            started=started,
        )

    def _build_model_discovery_missing_maas_department_result(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        started: float,
    ) -> ModelDiscoveryResult:
        return ModelDiscoveryResult(
            ok=False,
            provider=config.provider,
            base_url=config.base_url,
            latency_ms=self._latency_ms(started),
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=True,
                rate_limited=False,
            ),
            error_code="invalid_response",
            error_message=(
                "MAAS login response did not include user department information."
            ),
            retryable=False,
        )

    def _build_maas_model_discovery_payload(
        self,
        *,
        department: str,
    ) -> dict[str, object]:
        return {
            "area": DEFAULT_MAAS_DISCOVERY_AREA,
            "plugin_version": DEFAULT_MAAS_DISCOVERY_PLUGIN_VERSION,
            "application": DEFAULT_MAAS_DISCOVERY_APPLICATION,
            "ide": DEFAULT_MAAS_DISCOVERY_IDE,
            "plugin_name": DEFAULT_MAAS_DISCOVERY_PLUGIN_NAME,
            "department": department,
        }

    def _post_model_discovery_request(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
        checked_at: datetime,
        started: float,
        timeout_ms: int,
    ) -> httpx.Response | ModelDiscoveryResult:
        try:
            with create_sync_http_client(
                timeout_seconds=timeout_ms / 1000,
                connect_timeout_seconds=timeout_ms / 1000,
                ssl_verify=config.ssl_verify,
            ) as client:
                return client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )

    def _get_model_discovery_request(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        endpoint: str,
        headers: dict[str, str],
        checked_at: datetime,
        started: float,
        timeout_ms: int,
    ) -> httpx.Response | ModelDiscoveryResult:
        try:
            with create_sync_http_client(
                timeout_seconds=timeout_ms / 1000,
                connect_timeout_seconds=timeout_ms / 1000,
                ssl_verify=config.ssl_verify,
            ) as client:
                return client.get(
                    endpoint,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )

    def _build_model_discovery_result_from_response(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        response: httpx.Response,
        checked_at: datetime,
        started: float,
    ) -> ModelDiscoveryResult:
        latency_ms = self._latency_ms(started)
        response_payload = self._response_payload(response)
        if response.status_code >= 400:
            error_message = (
                self._extract_error_message(response_payload) or response.text
            )
            return self._build_model_discovery_http_error_result(
                config=config,
                checked_at=checked_at,
                latency_ms=latency_ms,
                status_code=response.status_code,
                error_message=error_message or "Model discovery failed.",
            )

        if response_payload is _INVALID_RESPONSE_PAYLOAD:
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned invalid JSON.",
                retryable=False,
            )

        if not isinstance(response_payload, dict | list):
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned a non-object JSON payload.",
                retryable=False,
            )

        model_entries = self._extract_model_entries(
            payload=response_payload,
            provider=config.provider,
        )
        if model_entries is None:
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned an invalid model catalog payload.",
                retryable=False,
            )

        return ModelDiscoveryResult(
            ok=True,
            provider=config.provider,
            base_url=config.base_url,
            latency_ms=latency_ms,
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=True,
                rate_limited=False,
            ),
            models=tuple(entry.model for entry in model_entries),
            model_entries=model_entries,
        )

    def _post_probe_request(
        self,
        *,
        config: ModelEndpointConfig,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
        checked_at: datetime,
        started: float,
        timeout_ms: int,
    ) -> httpx.Response | ModelConnectivityProbeResult:
        try:
            with create_sync_http_client(
                timeout_seconds=timeout_ms / 1000,
                connect_timeout_seconds=timeout_ms / 1000,
                ssl_verify=config.ssl_verify,
            ) as client:
                return client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )

    def _build_maas_login_error_result(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        started: float,
        error: MaaSLoginError,
    ) -> ModelConnectivityProbeResult:
        status_code = error.status_code
        error_message = str(error) or "MAAS login failed."
        if status_code is None or status_code < 400:
            return ModelConnectivityProbeResult(
                ok=False,
                provider=config.provider,
                model=config.model,
                latency_ms=self._latency_ms(started),
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message=error_message,
                retryable=False,
            )
        auth_valid = status_code not in {400, 401, 403}
        rate_limited = status_code == 429
        retryable = rate_limited or status_code >= 500
        error_code = (
            "auth_invalid" if not auth_valid else self._http_error_code(status_code)
        )
        return ModelConnectivityProbeResult(
            ok=False,
            provider=config.provider,
            model=config.model,
            latency_ms=self._latency_ms(started),
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=auth_valid,
                rate_limited=rate_limited,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def _get_codeagent_token_for_probe(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        started: float,
        timeout_ms: int,
        force_refresh: bool = False,
    ) -> str | ModelConnectivityProbeResult:
        try:
            auth_config = self._resolve_codeagent_auth_for_request(
                self._require_codeagent_auth_config(config.codeagent_auth)
            )
            return get_codeagent_token_service().get_token_sync(
                base_url=config.base_url,
                auth_config=auth_config,
                ssl_verify=config.ssl_verify,
                connect_timeout_seconds=timeout_ms / 1000,
                force_refresh=force_refresh,
            )
        except httpx.TimeoutException as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )
        except CodeAgentOAuthError as exc:
            return self._build_codeagent_oauth_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error=exc,
            )

    def _get_codeagent_token_for_verify(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        force_refresh: bool = False,
    ) -> str | CodeAgentAuthVerifyResult:
        try:
            auth_config = self._resolve_codeagent_auth_for_request(
                self._require_codeagent_auth_config(config.codeagent_auth)
            )
            return get_codeagent_token_service().get_token_sync(
                base_url=config.base_url,
                auth_config=auth_config,
                ssl_verify=config.ssl_verify,
                connect_timeout_seconds=config.connect_timeout_seconds,
                force_refresh=force_refresh,
            )
        except httpx.TimeoutException as exc:
            return CodeAgentAuthVerifyResult(
                status="error",
                checked_at=checked_at,
                detail=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return CodeAgentAuthVerifyResult(
                status="error",
                checked_at=checked_at,
                detail=str(exc) or "Failed to reach CodeAgent endpoint.",
            )
        except CodeAgentOAuthError as exc:
            return CodeAgentAuthVerifyResult(
                status=(
                    "reauth_required"
                    if self._is_codeagent_auth_invalid_error(exc)
                    else "error"
                ),
                checked_at=checked_at,
                detail=str(exc) or "CodeAgent OAuth request failed.",
            )

    def _get_codeagent_token_for_discovery(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        started: float,
        timeout_ms: int,
        force_refresh: bool = False,
    ) -> str | ModelDiscoveryResult:
        try:
            auth_config = self._resolve_codeagent_auth_for_request(
                self._require_codeagent_auth_config(config.codeagent_auth)
            )
            return get_codeagent_token_service().get_token_sync(
                base_url=config.base_url,
                auth_config=auth_config,
                ssl_verify=config.ssl_verify,
                connect_timeout_seconds=timeout_ms / 1000,
                force_refresh=force_refresh,
            )
        except httpx.TimeoutException as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_timeout",
                error_message=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return self._build_model_discovery_transport_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error_code="network_error",
                error_message=str(exc) or "Failed to reach model endpoint.",
            )
        except CodeAgentOAuthError as exc:
            return self._build_model_discovery_codeagent_oauth_error_result(
                config=config,
                checked_at=checked_at,
                started=started,
                error=exc,
            )

    def _resolve_codeagent_auth_for_request(
        self,
        auth_config: CodeAgentAuthConfig,
    ) -> CodeAgentAuthConfig:
        if auth_config.oauth_session_id is not None:
            token_result = get_codeagent_oauth_tokens(auth_config.oauth_session_id)
            if token_result is not None:
                resolved_auth = CodeAgentAuthConfig(
                    client_id=auth_config.client_id,
                    scope=auth_config.scope,
                    scope_resource=auth_config.scope_resource,
                    access_token=token_result.access_token,
                    refresh_token=token_result.refresh_token,
                    oauth_session_id=auth_config.oauth_session_id,
                )
                if (
                    auth_config.secret_config_dir is None
                    or auth_config.secret_owner_id is None
                ):
                    return resolved_auth
                return resolved_auth.with_secret_owner(
                    config_dir=auth_config.secret_config_dir,
                    owner_id=auth_config.secret_owner_id,
                )
        if auth_config.refresh_token is not None:
            return auth_config
        if auth_config.oauth_session_id is None:
            raise CodeAgentOAuthError(
                "CodeAgent refresh token is not configured.",
                status_code=None,
            )
        raise CodeAgentOAuthError(
            "CodeAgent OAuth session is missing, expired, or already consumed.",
            status_code=400,
        )

    @staticmethod
    def _require_codeagent_auth_config(
        auth_config: CodeAgentAuthConfig | None,
    ) -> CodeAgentAuthConfig:
        if auth_config is None:
            raise CodeAgentOAuthError(
                "CodeAgent auth is not configured.",
                status_code=None,
            )
        return auth_config

    @staticmethod
    def _send_codeagent_auth_verify_request(
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        token: str,
    ) -> httpx.Response | CodeAgentAuthVerifyResult:
        endpoint = f"{config.base_url.rstrip('/')}/chat/modles?checkUserPermission=TRUE"
        try:
            with create_sync_http_client(
                timeout_seconds=config.connect_timeout_seconds,
                connect_timeout_seconds=config.connect_timeout_seconds,
                ssl_verify=config.ssl_verify,
            ) as client:
                return client.get(
                    endpoint,
                    headers=build_codeagent_request_headers(token=token),
                )
        except httpx.TimeoutException as exc:
            return CodeAgentAuthVerifyResult(
                status="error",
                checked_at=checked_at,
                detail=str(exc) or "Connection timed out.",
            )
        except httpx.RequestError as exc:
            return CodeAgentAuthVerifyResult(
                status="error",
                checked_at=checked_at,
                detail=str(exc) or "Failed to reach CodeAgent endpoint.",
            )

    def _build_codeagent_auth_verify_http_error_result(
        self,
        *,
        checked_at: datetime,
        response: httpx.Response,
    ) -> CodeAgentAuthVerifyResult:
        return CodeAgentAuthVerifyResult(
            status="error",
            checked_at=checked_at,
            detail=self._extract_codeagent_auth_verify_error_message(response)
            or "Failed to verify CodeAgent authentication.",
        )

    def _build_codeagent_oauth_error_result(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        started: float,
        error: CodeAgentOAuthError,
    ) -> ModelConnectivityProbeResult:
        status_code = error.status_code
        error_message = str(error) or "CodeAgent OAuth request failed."
        if status_code is None or status_code < 400:
            if self._is_codeagent_auth_invalid_error(error):
                return ModelConnectivityProbeResult(
                    ok=False,
                    provider=config.provider,
                    model=config.model,
                    latency_ms=self._latency_ms(started),
                    checked_at=checked_at,
                    diagnostics=ModelConnectivityDiagnostics(
                        endpoint_reachable=True,
                        auth_valid=False,
                        rate_limited=False,
                    ),
                    error_code="auth_invalid",
                    error_message=error_message,
                    retryable=False,
                )
            return ModelConnectivityProbeResult(
                ok=False,
                provider=config.provider,
                model=config.model,
                latency_ms=self._latency_ms(started),
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message=error_message,
                retryable=False,
            )
        if self._is_codeagent_auth_invalid_error(error):
            return ModelConnectivityProbeResult(
                ok=False,
                provider=config.provider,
                model=config.model,
                latency_ms=self._latency_ms(started),
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=False,
                    rate_limited=False,
                ),
                error_code="auth_invalid",
                error_message=error_message,
                retryable=False,
            )
        return self._build_http_error_result(
            config=config,
            checked_at=checked_at,
            latency_ms=self._latency_ms(started),
            status_code=status_code,
            error_message=error_message,
        )

    def _build_model_discovery_codeagent_oauth_error_result(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        started: float,
        error: CodeAgentOAuthError,
    ) -> ModelDiscoveryResult:
        status_code = error.status_code
        error_message = str(error) or "CodeAgent OAuth request failed."
        if status_code is None or status_code < 400:
            if self._is_codeagent_auth_invalid_error(error):
                return ModelDiscoveryResult(
                    ok=False,
                    provider=config.provider,
                    base_url=config.base_url,
                    latency_ms=self._latency_ms(started),
                    checked_at=checked_at,
                    diagnostics=ModelConnectivityDiagnostics(
                        endpoint_reachable=True,
                        auth_valid=False,
                        rate_limited=False,
                    ),
                    error_code="auth_invalid",
                    error_message=error_message,
                    retryable=False,
                )
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=self._latency_ms(started),
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message=error_message,
                retryable=False,
            )
        if self._is_codeagent_auth_invalid_error(error):
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=self._latency_ms(started),
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=False,
                    rate_limited=False,
                ),
                error_code="auth_invalid",
                error_message=error_message,
                retryable=False,
            )
        return self._build_model_discovery_http_error_result(
            config=config,
            checked_at=checked_at,
            latency_ms=self._latency_ms(started),
            status_code=status_code,
            error_message=error_message,
        )

    def _build_model_discovery_maas_login_error_result(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        started: float,
        error: MaaSLoginError,
    ) -> ModelDiscoveryResult:
        status_code = error.status_code
        error_message = str(error) or "MAAS login failed."
        if status_code is None or status_code < 400:
            return ModelDiscoveryResult(
                ok=False,
                provider=config.provider,
                base_url=config.base_url,
                latency_ms=self._latency_ms(started),
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message=error_message,
                retryable=False,
            )
        auth_valid = status_code not in {400, 401, 403}
        rate_limited = status_code == 429
        retryable = rate_limited or status_code >= 500
        error_code = (
            "auth_invalid" if not auth_valid else self._http_error_code(status_code)
        )
        return ModelDiscoveryResult(
            ok=False,
            provider=config.provider,
            base_url=config.base_url,
            latency_ms=self._latency_ms(started),
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=auth_valid,
                rate_limited=rate_limited,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def _build_probe_result_from_response(
        self,
        *,
        config: ModelEndpointConfig,
        response: httpx.Response,
        checked_at: datetime,
        started: float,
    ) -> ModelConnectivityProbeResult:
        latency_ms = self._latency_ms(started)
        response_payload = self._response_payload(response)
        if response.status_code >= 400:
            error_message = (
                self._extract_error_message(response_payload) or response.text
            )
            return self._build_http_error_result(
                config=config,
                checked_at=checked_at,
                latency_ms=latency_ms,
                status_code=response.status_code,
                error_message=error_message or "Model connectivity check failed.",
            )
        if config.provider == ProviderType.CODEAGENT and self._is_event_stream_response(
            response
        ):
            if response_payload is _EVENT_STREAM_PLAIN_TEXT_SUCCESS:
                return ModelConnectivityProbeResult(
                    ok=True,
                    provider=config.provider,
                    model=config.model,
                    latency_ms=latency_ms,
                    checked_at=checked_at,
                    diagnostics=ModelConnectivityDiagnostics(
                        endpoint_reachable=True,
                        auth_valid=True,
                        rate_limited=False,
                    ),
                    token_usage=None,
                )
            if response_payload is _INVALID_RESPONSE_PAYLOAD:
                return ModelConnectivityProbeResult(
                    ok=False,
                    provider=config.provider,
                    model=config.model,
                    latency_ms=latency_ms,
                    checked_at=checked_at,
                    diagnostics=ModelConnectivityDiagnostics(
                        endpoint_reachable=True,
                        auth_valid=True,
                        rate_limited=False,
                    ),
                    error_code="invalid_response",
                    error_message="Provider returned invalid SSE payload.",
                    retryable=False,
                )
            error_message = self._extract_error_message(response_payload)
            if error_message is not None:
                return ModelConnectivityProbeResult(
                    ok=False,
                    provider=config.provider,
                    model=config.model,
                    latency_ms=latency_ms,
                    checked_at=checked_at,
                    diagnostics=ModelConnectivityDiagnostics(
                        endpoint_reachable=True,
                        auth_valid=True,
                        rate_limited=False,
                    ),
                    error_code="invalid_response",
                    error_message=error_message,
                    retryable=False,
                )
            token_usage = None
            if isinstance(response_payload, dict):
                token_usage = self._extract_token_usage(response_payload.get("usage"))
            return ModelConnectivityProbeResult(
                ok=True,
                provider=config.provider,
                model=config.model,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                token_usage=token_usage,
            )
        if response_payload is _INVALID_RESPONSE_PAYLOAD:
            return ModelConnectivityProbeResult(
                ok=False,
                provider=config.provider,
                model=config.model,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned invalid JSON.",
                retryable=False,
            )
        if not isinstance(response_payload, dict):
            return ModelConnectivityProbeResult(
                ok=False,
                provider=config.provider,
                model=config.model,
                latency_ms=latency_ms,
                checked_at=checked_at,
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="invalid_response",
                error_message="Provider returned a non-object JSON payload.",
                retryable=False,
            )
        usage_payload = response_payload.get("usage")
        token_usage = self._extract_token_usage(usage_payload)
        return ModelConnectivityProbeResult(
            ok=True,
            provider=config.provider,
            model=config.model,
            latency_ms=latency_ms,
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=True,
                rate_limited=False,
            ),
            token_usage=token_usage,
        )

    def _build_transport_error_result(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        started: float,
        error_code: str,
        error_message: str,
    ) -> ModelConnectivityProbeResult:
        return ModelConnectivityProbeResult(
            ok=False,
            provider=config.provider,
            model=config.model,
            latency_ms=self._latency_ms(started),
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=False,
                auth_valid=True,
                rate_limited=False,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=True,
        )

    def _build_model_discovery_transport_error_result(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        started: float,
        error_code: str,
        error_message: str,
    ) -> ModelDiscoveryResult:
        return ModelDiscoveryResult(
            ok=False,
            provider=config.provider,
            base_url=config.base_url,
            latency_ms=self._latency_ms(started),
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=False,
                auth_valid=True,
                rate_limited=False,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=True,
        )

    def _build_http_error_result(
        self,
        *,
        config: ModelEndpointConfig,
        checked_at: datetime,
        latency_ms: int,
        status_code: int,
        error_message: str,
    ) -> ModelConnectivityProbeResult:
        auth_valid = status_code not in {401, 403}
        rate_limited = status_code == 429
        retryable = rate_limited or status_code >= 500
        error_code = self._http_error_code(status_code)
        return ModelConnectivityProbeResult(
            ok=False,
            provider=config.provider,
            model=config.model,
            latency_ms=latency_ms,
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=auth_valid,
                rate_limited=rate_limited,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def _build_model_discovery_http_error_result(
        self,
        *,
        config: ModelDiscoveryResolvedConfig,
        checked_at: datetime,
        latency_ms: int,
        status_code: int,
        error_message: str,
    ) -> ModelDiscoveryResult:
        auth_valid = status_code not in {401, 403}
        rate_limited = status_code == 429
        retryable = rate_limited or status_code >= 500
        error_code = self._http_error_code(status_code)
        return ModelDiscoveryResult(
            ok=False,
            provider=config.provider,
            base_url=config.base_url,
            latency_ms=latency_ms,
            checked_at=checked_at,
            diagnostics=ModelConnectivityDiagnostics(
                endpoint_reachable=True,
                auth_valid=auth_valid,
                rate_limited=rate_limited,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def _extract_token_usage(
        self,
        usage_payload: object,
    ) -> ModelConnectivityTokenUsage:
        usage_dict = (
            cast(dict[str, object], usage_payload)
            if isinstance(usage_payload, dict)
            else {}
        )
        prompt_tokens = self._safe_int(usage_dict.get("prompt_tokens"))
        completion_tokens = self._safe_int(usage_dict.get("completion_tokens"))
        total_tokens = self._safe_int(usage_dict.get("total_tokens"))
        return ModelConnectivityTokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens
            if total_tokens > 0
            else prompt_tokens + completion_tokens,
        )

    def _extract_error_message(self, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("message", "detail", "error_description", "error_msg"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error_payload, str) and error_payload.strip():
            return error_payload.strip()
        return None

    def _extract_codeagent_auth_verify_error_message(
        self,
        response: httpx.Response,
    ) -> str | None:
        payload = self._response_payload(response)
        return self._extract_error_message(payload) or response.text.strip() or None

    def _extract_model_entries(
        self,
        *,
        payload: dict[str, object] | list[object],
        provider: ProviderType,
    ) -> tuple[ModelDiscoveryEntry, ...] | None:
        if provider == ProviderType.CODEAGENT:
            return self._extract_codeagent_model_entries(payload)
        if provider == ProviderType.MAAS:
            return self._extract_maas_model_entries(payload)
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        model_entries: list[ModelDiscoveryEntry] = []
        seen_model_ids: set[str] = set()
        for entry in data:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                continue
            normalized = model_id.strip()
            if not normalized or normalized in seen_model_ids:
                continue
            seen_model_ids.add(normalized)
            model_entries.append(
                ModelDiscoveryEntry(
                    model=normalized,
                    context_window=(
                        self._extract_context_window(entry)
                        or infer_known_context_window(
                            provider=provider,
                            model=normalized,
                        )
                    ),
                    capabilities=resolve_model_capabilities(
                        provider=provider,
                        base_url="",
                        model_name=normalized,
                        metadata=entry,
                    ),
                    input_modalities=resolve_model_input_modalities(
                        provider=provider,
                        base_url="",
                        model_name=normalized,
                        metadata=entry,
                    ),
                )
            )
        model_entries.sort(key=lambda item: item.model)
        return tuple(model_entries)

    def _extract_codeagent_model_entries(
        self,
        payload: dict[str, object] | list[object],
    ) -> tuple[ModelDiscoveryEntry, ...] | None:
        entries_payload: list[object] | None = None
        if isinstance(payload, list):
            entries_payload = payload
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                entries_payload = data
            elif isinstance(payload.get("models"), list):
                entries_payload = cast(list[object], payload.get("models"))
        if entries_payload is None:
            return None
        model_entries: list[ModelDiscoveryEntry] = []
        seen_model_ids: set[str] = set()
        for entry in entries_payload:
            model_id: str | None = None
            metadata: object | None = entry
            if isinstance(entry, str):
                model_id = entry.strip()
            elif isinstance(entry, dict):
                for field_name in ("name", "id", "model"):
                    value = entry.get(field_name)
                    if isinstance(value, str) and value.strip():
                        model_id = value.strip()
                        break
            if model_id is None or not model_id or model_id in seen_model_ids:
                continue
            seen_model_ids.add(model_id)
            model_entries.append(
                ModelDiscoveryEntry(
                    model=model_id,
                    context_window=infer_known_context_window(
                        provider=ProviderType.CODEAGENT,
                        model=model_id,
                    ),
                    capabilities=resolve_model_capabilities(
                        provider=ProviderType.CODEAGENT,
                        base_url="",
                        model_name=model_id,
                        metadata=metadata,
                    ),
                    input_modalities=resolve_model_input_modalities(
                        provider=ProviderType.CODEAGENT,
                        base_url="",
                        model_name=model_id,
                        metadata=metadata,
                    ),
                )
            )
        model_entries.sort(key=lambda item: item.model)
        return tuple(model_entries)

    def _extract_maas_model_entries(
        self,
        payload: dict[str, object] | list[object],
    ) -> tuple[ModelDiscoveryEntry, ...] | None:
        if not isinstance(payload, dict):
            return None
        has_supported_section = False
        model_ids: set[str] = set()

        user_model_list = payload.get("user_model_list")
        if isinstance(user_model_list, list):
            has_supported_section = True
            self._collect_maas_model_ids(
                models=user_model_list,
                target=model_ids,
            )

        plugin_config = payload.get("plugin_config")
        if isinstance(plugin_config, list):
            has_supported_section = True
            for plugin_index, plugin_entry in enumerate(plugin_config):
                if not isinstance(plugin_entry, dict):
                    continue
                config_payload = plugin_entry.get("config")
                if not isinstance(config_payload, str):
                    continue
                parsed_config = self._parse_maas_plugin_config(
                    raw_config=config_payload,
                    plugin_index=plugin_index,
                )
                if parsed_config is None:
                    continue
                for config_item in parsed_config:
                    if not isinstance(config_item, dict):
                        continue
                    for field_name in (
                        "composor_act_mode_model_list",
                        "composor_plan_mode_model_list",
                        "user_model_list",
                    ):
                        nested_models = config_item.get(field_name)
                        if not isinstance(nested_models, list):
                            continue
                        self._collect_maas_model_ids(
                            models=nested_models,
                            target=model_ids,
                        )

        if not has_supported_section:
            return None

        return tuple(
            ModelDiscoveryEntry(
                model=model_id,
                capabilities=resolve_model_capabilities(
                    provider=ProviderType.MAAS,
                    base_url="",
                    model_name=model_id,
                ),
                input_modalities=resolve_model_input_modalities(
                    provider=ProviderType.MAAS,
                    base_url="",
                    model_name=model_id,
                ),
            )
            for model_id in sorted(model_ids)
        )

    def _parse_maas_plugin_config(
        self,
        *,
        raw_config: str,
        plugin_index: int,
    ) -> list[object] | None:
        try:
            parsed = cast(object, json.loads(raw_config))
        except ValueError:
            LOGGER.warning(
                "Ignoring invalid MAAS discovery plugin config JSON.",
                extra={
                    "event": "providers.maas.discovery.invalid_plugin_config",
                    "plugin_index": plugin_index,
                },
            )
            return None
        if not isinstance(parsed, list):
            LOGGER.warning(
                "Ignoring MAAS discovery plugin config with non-list payload.",
                extra={
                    "event": "providers.maas.discovery.invalid_plugin_config_shape",
                    "plugin_index": plugin_index,
                },
            )
            return None
        return parsed

    def _collect_maas_model_ids(
        self,
        *,
        models: list[object],
        target: set[str],
    ) -> None:
        for model_entry in models:
            if not isinstance(model_entry, dict):
                continue
            model_id = model_entry.get("model_id")
            if not isinstance(model_id, str):
                continue
            normalized = model_id.strip()
            if self._is_valid_maas_model_id(normalized):
                target.add(normalized)

    def _is_valid_maas_model_id(self, model_id: str) -> bool:
        if not model_id:
            return False
        if model_id.isdigit():
            return False
        if ":" in model_id:
            return False
        return True

    def _extract_context_window(self, entry: dict[str, object]) -> int | None:
        direct_keys = (
            "context_window",
            "contextWindow",
            "context_length",
            "contextLength",
            "max_context_length",
            "maxContextLength",
            "input_token_limit",
            "inputTokenLimit",
        )
        for key in direct_keys:
            value = entry.get(key)
            if isinstance(value, int) and value > 0:
                return value
        for nested_key in ("limits", "limit", "capabilities", "metadata"):
            nested = entry.get(nested_key)
            if not isinstance(nested, dict):
                continue
            for key in direct_keys:
                value = nested.get(key)
                if isinstance(value, int) and value > 0:
                    return value
            context_limit = nested.get("context")
            if isinstance(context_limit, int) and context_limit > 0:
                return context_limit
        return None

    def _response_payload(self, response: httpx.Response) -> object:
        try:
            return cast(object, response.json())
        except ValueError:
            return self._event_stream_payload(response.text)

    def _event_stream_payload(self, raw_text: str) -> object:
        normalized = str(raw_text or "").strip()
        if not normalized:
            return _INVALID_RESPONSE_PAYLOAD
        data_chunks: list[str] = []
        for raw_line in normalized.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            data_chunks.append(chunk)
        if not data_chunks:
            return _INVALID_RESPONSE_PAYLOAD
        for chunk in reversed(data_chunks):
            try:
                return cast(object, json.loads(chunk))
            except ValueError:
                if chunk.casefold() == "pong":
                    return _EVENT_STREAM_PLAIN_TEXT_SUCCESS
                continue
        return _INVALID_RESPONSE_PAYLOAD

    def _is_event_stream_response(self, response: httpx.Response) -> bool:
        content_type = response.headers.get("content-type", "")
        return "text/event-stream" in content_type.casefold()

    def _http_error_code(self, status_code: int) -> str:
        if status_code in {401, 403}:
            return "auth_invalid"
        if status_code == 404:
            return "model_not_found"
        if status_code == 429:
            return "rate_limited"
        if status_code >= 500:
            return "provider_error"
        return "request_invalid"

    @staticmethod
    def _is_codeagent_auth_invalid_error(error: CodeAgentOAuthError) -> bool:
        return error.auth_invalid

    def _latency_ms(self, started: float) -> int:
        return max(0, int((perf_counter() - started) * 1000))

    def _safe_int(self, value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        return 0


def _resolve_runtime_profile_name(
    *,
    runtime: RuntimeConfig,
    requested_profile_name: str,
) -> str | None:
    normalized_name = requested_profile_name.strip()
    if normalized_name == "default":
        return runtime.default_model_profile
    if normalized_name in runtime.llm_profiles:
        return normalized_name
    return None
