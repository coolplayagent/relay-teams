# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
    SamplingConfig,
)
from agent_teams.providers.model_header_utils import (
    normalize_model_request_headers_payload,
)


def _normalize_acp_model_profile_headers(
    raw_value: JsonValue | None,
) -> tuple[ModelRequestHeader, ...]:
    if raw_value is None:
        return ()
    if isinstance(raw_value, dict):
        shorthand_headers: list[dict[str, JsonValue]] = []
        for raw_name, raw_header_value in raw_value.items():
            shorthand_headers.append(
                {
                    "name": str(raw_name),
                    "value": raw_header_value,
                }
            )
        return normalize_model_request_headers_payload(shorthand_headers)
    return normalize_model_request_headers_payload(raw_value)


class GatewayModelProfileOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="default", min_length=1)
    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    headers: tuple[ModelRequestHeader, ...] = ()
    ssl_verify: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    context_window: int | None = Field(default=None, ge=1)
    connect_timeout_seconds: float | None = Field(default=None, gt=0.0, le=300.0)

    @classmethod
    def from_acp_payload(
        cls, payload: dict[str, JsonValue]
    ) -> "GatewayModelProfileOverride":
        def _pick_str(*keys: str) -> str | None:
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        def _pick_number(*keys: str) -> float | int | None:
            for key in keys:
                value = payload.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return value
            return None

        provider_raw = _pick_str("provider") or ProviderType.OPENAI_COMPATIBLE.value
        base_url = _pick_str("baseUrl", "base_url")
        api_key = _pick_str("apiKey", "api_key")
        model = _pick_str("model")
        headers = _normalize_acp_model_profile_headers(payload.get("headers"))
        if not base_url or not model or (not api_key and not headers):
            raise ValueError(
                "modelProfileOverride requires model, baseUrl, and apiKey or headers"
            )

        ssl_verify_value = payload.get("sslVerify", payload.get("ssl_verify"))
        ssl_verify: bool | None
        if isinstance(ssl_verify_value, bool):
            ssl_verify = ssl_verify_value
        else:
            ssl_verify = None

        return cls(
            name=_pick_str("name") or "default",
            provider=ProviderType(provider_raw),
            model=model,
            base_url=base_url,
            api_key=api_key,
            headers=headers,
            ssl_verify=ssl_verify,
            temperature=_pick_number("temperature"),
            top_p=_pick_number("topP", "top_p"),
            max_tokens=(
                int(max_tokens)
                if (max_tokens := _pick_number("maxTokens", "max_tokens")) is not None
                else None
            ),
            context_window=(
                int(context_window)
                if (context_window := _pick_number("contextWindow", "context_window"))
                is not None
                else None
            ),
            connect_timeout_seconds=(
                float(timeout)
                if (
                    timeout := _pick_number(
                        "connectTimeoutSeconds", "connect_timeout_seconds"
                    )
                )
                is not None
                else None
            ),
        )

    def to_model_endpoint_config(self) -> ModelEndpointConfig:
        sampling_defaults = SamplingConfig()
        return ModelEndpointConfig(
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            headers=self.headers,
            ssl_verify=self.ssl_verify,
            context_window=self.context_window,
            connect_timeout_seconds=(
                self.connect_timeout_seconds
                if self.connect_timeout_seconds is not None
                else DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
            ),
            sampling=SamplingConfig(
                temperature=(
                    self.temperature
                    if self.temperature is not None
                    else sampling_defaults.temperature
                ),
                top_p=self.top_p if self.top_p is not None else sampling_defaults.top_p,
                max_tokens=self.max_tokens,
                top_k=sampling_defaults.top_k,
            ),
        )

    def to_public_state(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "provider": self.provider.value,
            "model": self.model,
            "baseUrl": self.base_url,
            "headers": [
                header.model_copy(
                    update={"value": None, "configured": True}
                ).model_dump(mode="json")
                for header in self.headers
            ],
            "sslVerify": self.ssl_verify,
            "temperature": self.temperature,
            "topP": self.top_p,
            "maxTokens": self.max_tokens,
            "contextWindow": self.context_window,
            "connectTimeoutSeconds": self.connect_timeout_seconds,
        }
