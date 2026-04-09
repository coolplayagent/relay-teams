# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from time import perf_counter
from typing import cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.net.clients import create_sync_http_client
from relay_teams.providers.maas_auth import MaaSLoginError, get_maas_token_service
from relay_teams.providers.known_model_context_windows import (
    infer_known_context_window,
)
from relay_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAAS_APP_ID,
    MaaSAuthConfig,
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
    SamplingConfig,
)
from relay_teams.providers.openai_support import build_model_request_headers
from relay_teams.sessions.runs.runtime_config import RuntimeConfig


_INVALID_RESPONSE_PAYLOAD = object()
_MAX_PROBE_TIMEOUT_MS = 300_000


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
    ssl_verify: bool | None = None
    connect_timeout_seconds: float = Field(gt=0.0, le=300.0)


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
            return ModelDiscoveryResult(
                ok=False,
                provider=resolved_config.provider,
                base_url=resolved_config.base_url,
                latency_ms=0,
                checked_at=datetime.now(timezone.utc),
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=True,
                    auth_valid=True,
                    rate_limited=False,
                ),
                error_code="unsupported_provider",
                error_message="MAAS model discovery is not supported. Enter the model name manually.",
                retryable=False,
            )
        if _uses_openai_compatible_transport(resolved_config.provider):
            return self._discover_openai_compatible_models(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        raise ValueError(
            f"Model discovery is not supported for provider '{resolved_config.provider.value}'."
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
            missing_fields: list[str] = []
            if override.model is None:
                missing_fields.append("model")
            if override.base_url is None:
                missing_fields.append("base_url")
            if (
                override.provider or ProviderType.OPENAI_COMPATIBLE
            ) == ProviderType.MAAS:
                if override.maas_auth is None:
                    missing_fields.append("maas_auth")
            elif override.api_key is None and not override.headers:
                missing_fields.append("api_key or headers")
            if missing_fields:
                joined_fields = ", ".join(missing_fields)
                raise ValueError(
                    f"Override config is missing required fields: {joined_fields}."
                )
            override_model = cast(str, override.model)
            override_base_url = cast(str, override.base_url)
            return ModelEndpointConfig(
                provider=override.provider or ProviderType.OPENAI_COMPATIBLE,
                model=override_model,
                base_url=override_base_url,
                api_key=override.api_key,
                headers=override.headers,
                maas_auth=override.maas_auth,
                ssl_verify=override.ssl_verify,
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
            missing_fields: list[str] = []
            if override.base_url is None:
                missing_fields.append("base_url")
            if (
                override.provider or ProviderType.OPENAI_COMPATIBLE
            ) == ProviderType.MAAS:
                if override.maas_auth is None:
                    missing_fields.append("maas_auth")
            elif override.api_key is None and not override.headers:
                missing_fields.append("api_key or headers")
            if missing_fields:
                joined_fields = ", ".join(missing_fields)
                raise ValueError(
                    f"Override config is missing required fields: {joined_fields}."
                )
            return ModelDiscoveryResolvedConfig(
                provider=override.provider or ProviderType.OPENAI_COMPATIBLE,
                base_url=cast(str, override.base_url),
                api_key=override.api_key,
                headers=override.headers,
                maas_auth=override.maas_auth,
                ssl_verify=override.ssl_verify,
                connect_timeout_seconds=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
            )

        resolved_override = override or ModelConnectivityProbeOverride()
        return ModelDiscoveryResolvedConfig(
            provider=resolved_override.provider or base_config.provider,
            base_url=resolved_override.base_url or base_config.base_url,
            api_key=resolved_override.api_key or base_config.api_key,
            headers=resolved_override.headers or base_config.headers,
            maas_auth=self._merge_maas_auth(
                base_maas_auth=base_config.maas_auth,
                override_maas_auth=resolved_override.maas_auth,
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
        return ModelEndpointConfig(
            provider=override.provider or base_config.provider,
            model=override.model or base_config.model,
            base_url=override.base_url or base_config.base_url,
            api_key=override.api_key or base_config.api_key,
            headers=override.headers or base_config.headers,
            maas_auth=self._merge_maas_auth(
                base_maas_auth=base_config.maas_auth,
                override_maas_auth=override.maas_auth,
            ),
            ssl_verify=(
                override.ssl_verify
                if override.ssl_verify is not None
                else base_config.ssl_verify
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

        if not isinstance(response_payload, dict):
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
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        return None

    def _extract_model_entries(
        self,
        *,
        payload: dict[str, object],
        provider: ProviderType,
    ) -> tuple[ModelDiscoveryEntry, ...] | None:
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
                )
            )
        model_entries.sort(key=lambda item: item.model)
        return tuple(model_entries)

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
                continue
        return _INVALID_RESPONSE_PAYLOAD

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
