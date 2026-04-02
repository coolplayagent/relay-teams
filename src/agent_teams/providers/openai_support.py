# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping

import httpx
from openai import AsyncOpenAI
from pydantic_ai.providers.openai import OpenAIProvider

from agent_teams.providers.model_config import ModelEndpointConfig, ModelRequestHeader


def build_model_request_headers(
    config: ModelEndpointConfig,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if config.api_key is not None:
        headers["Authorization"] = f"Bearer {config.api_key}"
    for entry in config.headers:
        if entry.value is None:
            continue
        existing_name = _find_header_name(headers, entry.name)
        if existing_name is not None:
            headers.pop(existing_name)
        headers[entry.name] = entry.value
    if extra_headers is not None:
        for name, value in extra_headers.items():
            existing_name = _find_header_name(headers, name)
            if existing_name is not None:
                headers.pop(existing_name)
            headers[name] = value
    return headers


def build_openai_provider(
    *,
    config: ModelEndpointConfig,
    http_client: httpx.AsyncClient,
) -> OpenAIProvider:
    return build_openai_provider_for_endpoint(
        base_url=config.base_url,
        api_key=config.api_key,
        headers=config.headers,
        http_client=http_client,
    )


def build_openai_provider_for_endpoint(
    *,
    base_url: str,
    api_key: str | None,
    headers: tuple[ModelRequestHeader, ...],
    http_client: httpx.AsyncClient,
) -> OpenAIProvider:
    custom_headers = _custom_headers_without_authorization(headers)
    openai_client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or "",
        default_headers=custom_headers or None,
        http_client=http_client,
    )
    return OpenAIProvider(openai_client=openai_client)


def _custom_headers_without_authorization(
    config: ModelEndpointConfig | tuple[ModelRequestHeader, ...],
) -> dict[str, str]:
    headers: dict[str, str] = {}
    binding_entries = (
        config.headers if isinstance(config, ModelEndpointConfig) else config
    )
    for entry in binding_entries:
        if entry.value is None:
            continue
        if entry.name.casefold() == "authorization":
            headers["Authorization"] = entry.value
            continue
        existing_name = _find_header_name(headers, entry.name)
        if existing_name is not None:
            headers.pop(existing_name)
        headers[entry.name] = entry.value
    return headers


def _find_header_name(headers: Mapping[str, str], name: str) -> str | None:
    normalized_name = name.casefold()
    for existing_name in headers:
        if existing_name.casefold() == normalized_name:
            return existing_name
    return None
