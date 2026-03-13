# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from time import perf_counter
from typing import cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent_teams.env.proxy_http_client import create_proxy_http_client
from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderType,
    SamplingConfig,
)
from agent_teams.runs.runtime_config import RuntimeConfig


_INVALID_RESPONSE_PAYLOAD = object()
_MAX_PROBE_TIMEOUT_MS = 300_000


class ModelConnectivityProbeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType | None = None
    model: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    ssl_verify: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)


class ModelConnectivityProbeRequest(BaseModel):
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
        if resolved_config.provider == ProviderType.OPENAI_COMPATIBLE:
            return self._probe_openai_compatible(
                config=resolved_config,
                timeout_ms=timeout_ms,
            )
        raise ValueError(
            f"Connectivity probe is not supported for provider '{resolved_config.provider.value}'."
        )

    def _resolve_endpoint_config(
        self,
        request: ModelConnectivityProbeRequest,
    ) -> ModelEndpointConfig:
        base_config: ModelEndpointConfig | None = None
        if request.profile_name is not None:
            base_config = self._get_runtime().llm_profiles.get(request.profile_name)
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
            if override.api_key is None:
                missing_fields.append("api_key")
            if missing_fields:
                joined_fields = ", ".join(missing_fields)
                raise ValueError(
                    f"Override config is missing required fields: {joined_fields}."
                )
            override_model = cast(str, override.model)
            override_base_url = cast(str, override.base_url)
            override_api_key = cast(str, override.api_key)
            return ModelEndpointConfig(
                provider=override.provider or ProviderType.OPENAI_COMPATIBLE,
                model=override_model,
                base_url=override_base_url,
                api_key=override_api_key,
                ssl_verify=override.ssl_verify,
                sampling=SamplingConfig(
                    temperature=(
                        override.temperature
                        if override.temperature is not None
                        else 0.2
                    ),
                    top_p=override.top_p if override.top_p is not None else 1.0,
                    max_tokens=(
                        override.max_tokens if override.max_tokens is not None else 1
                    ),
                ),
            )

        return self._merge_config(base_config=base_config, override=request.override)

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

    def _resolve_timeout_ms(
        self,
        *,
        request: ModelConnectivityProbeRequest,
        config: ModelEndpointConfig,
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

    def _probe_openai_compatible(
        self,
        *,
        config: ModelEndpointConfig,
        timeout_ms: int,
    ) -> ModelConnectivityProbeResult:
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
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
            with create_proxy_http_client(
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

    def _response_payload(self, response: httpx.Response) -> object:
        try:
            return cast(object, response.json())
        except ValueError:
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
