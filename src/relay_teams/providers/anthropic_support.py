# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import httpx
from anthropic import AsyncAnthropic
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from relay_teams.providers.model_config import (
    DEFAULT_ANTHROPIC_BASE_URL,
    ModelEndpointConfig,
    ModelRequestHeader,
)

ANTHROPIC_VERSION = "2023-06-01"


def normalize_anthropic_sdk_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        return DEFAULT_ANTHROPIC_BASE_URL
    if normalized.lower().endswith("/v1"):
        return normalized[:-3].rstrip("/")
    return normalized


def anthropic_api_endpoint(base_url: str, endpoint_path: str) -> str:
    normalized_base = base_url.strip().rstrip("/") or DEFAULT_ANTHROPIC_BASE_URL
    normalized_path = endpoint_path.strip().lstrip("/")
    if normalized_base.lower().endswith("/v1"):
        return f"{normalized_base}/{normalized_path}"
    return f"{normalized_base}/v1/{normalized_path}"


def build_anthropic_request_headers(
    config: ModelEndpointConfig,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = _configured_headers(config.headers)
    api_key = config.api_key or _extract_api_key_from_headers(headers)
    if api_key is not None:
        _set_header(headers, "x-api-key", api_key)
    _set_header(headers, "anthropic-version", ANTHROPIC_VERSION)
    if extra_headers is not None:
        for name, value in extra_headers.items():
            _set_header(headers, name, value)
    return headers


def build_anthropic_provider(
    *,
    config: ModelEndpointConfig,
    http_client: httpx.AsyncClient,
) -> AnthropicProvider:
    headers = _configured_headers(config.headers)
    api_key = config.api_key or _extract_api_key_from_headers(headers)
    _remove_header(headers, "x-api-key")
    _remove_header(headers, "anthropic-version")
    client = AsyncAnthropic(
        api_key=api_key,
        base_url=normalize_anthropic_sdk_base_url(config.base_url),
        default_headers=headers or None,
        http_client=http_client,
        max_retries=0,
    )
    if api_key is None:
        client.api_key = None
        client.auth_token = None
    return AnthropicProvider(anthropic_client=client)


def build_anthropic_model(
    *,
    config: ModelEndpointConfig,
    http_client: httpx.AsyncClient,
) -> AnthropicModel:
    return AnthropicModel(
        config.model,
        provider=build_anthropic_provider(config=config, http_client=http_client),
    )


def _configured_headers(headers: tuple[ModelRequestHeader, ...]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for entry in headers:
        if entry.value is None:
            continue
        _set_header(resolved, entry.name, entry.value)
    return resolved


def _extract_api_key_from_headers(headers: Mapping[str, str]) -> str | None:
    for name, value in headers.items():
        if name.casefold() == "x-api-key" and value.strip():
            return value.strip()
    return None


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    existing_name = _find_header_name(headers, name)
    if existing_name is not None:
        headers.pop(existing_name)
    headers[name] = value


def _remove_header(headers: dict[str, str], name: str) -> None:
    existing_name = _find_header_name(headers, name)
    if existing_name is not None:
        headers.pop(existing_name)


def _find_header_name(headers: Mapping[str, str], name: str) -> str | None:
    normalized_name = name.casefold()
    for existing_name in headers:
        if existing_name.casefold() == normalized_name:
            return existing_name
    return None
