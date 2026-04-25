# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping

import httpx
from openai import AsyncOpenAI
from pydantic_ai.providers.openai import OpenAIProvider

from relay_teams.providers.maas_auth import (
    build_maas_openai_client,
    maas_reserved_header_names,
)
from relay_teams.providers.codeagent_auth import (
    build_codeagent_openai_client,
)
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    MaaSAuthConfig,
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
)


def build_model_request_headers(
    config: ModelEndpointConfig,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    reserved_maas_headers = maas_reserved_header_names()
    if config.api_key is not None and config.provider != ProviderType.MAAS:
        headers["Authorization"] = f"Bearer {config.api_key}"
    for entry in config.headers:
        if entry.value is None:
            continue
        if (
            config.provider == ProviderType.MAAS
            and entry.name.casefold() in reserved_maas_headers
        ):
            continue
        existing_name = _find_header_name(headers, entry.name)
        if existing_name is not None:
            headers.pop(existing_name)
        headers[entry.name] = entry.value
    if extra_headers is not None:
        for name, value in extra_headers.items():
            if (
                config.provider == ProviderType.MAAS
                and name.casefold() in reserved_maas_headers
            ):
                continue
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
        provider_type=config.provider,
        maas_auth=config.maas_auth,
        codeagent_auth=config.codeagent_auth,
        ssl_verify=config.ssl_verify,
        connect_timeout_seconds=config.connect_timeout_seconds,
    )


def build_openai_provider_for_endpoint(
    *,
    base_url: str,
    api_key: str | None,
    headers: tuple[ModelRequestHeader, ...],
    http_client: httpx.AsyncClient,
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE,
    maas_auth: MaaSAuthConfig | None = None,
    codeagent_auth: CodeAgentAuthConfig | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float = 15.0,
) -> OpenAIProvider:
    custom_headers = _custom_headers_without_authorization(
        headers,
        provider_type=provider_type,
    )
    if provider_type == ProviderType.MAAS:
        if maas_auth is None:
            raise ValueError("MAAS provider requires maas_auth configuration.")
        openai_client = build_maas_openai_client(
            base_url=base_url,
            auth_config=maas_auth,
            default_headers=custom_headers or None,
            http_client=http_client,
            connect_timeout_seconds=connect_timeout_seconds,
            ssl_verify=ssl_verify,
        )
        return OpenAIProvider(openai_client=openai_client)
    if provider_type == ProviderType.CODEAGENT:
        if codeagent_auth is None:
            raise ValueError(
                "CodeAgent provider requires codeagent_auth configuration."
            )
        openai_client = build_codeagent_openai_client(
            base_url=base_url,
            auth_config=codeagent_auth,
            default_headers=custom_headers or None,
            http_client=http_client,
            connect_timeout_seconds=connect_timeout_seconds,
            ssl_verify=ssl_verify,
        )
        return OpenAIProvider(openai_client=openai_client)
    openai_client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or "",
        default_headers=custom_headers or None,
        http_client=http_client,
    )
    return OpenAIProvider(openai_client=openai_client)


def _custom_headers_without_authorization(
    config: ModelEndpointConfig | tuple[ModelRequestHeader, ...],
    *,
    provider_type: ProviderType | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    binding_entries = (
        config.headers if isinstance(config, ModelEndpointConfig) else config
    )
    resolved_provider_type = (
        config.provider if isinstance(config, ModelEndpointConfig) else provider_type
    )
    reserved_maas_headers = maas_reserved_header_names()
    for entry in binding_entries:
        if entry.value is None:
            continue
        normalized_name = entry.name.casefold()
        if normalized_name == "authorization":
            if resolved_provider_type == ProviderType.MAAS:
                continue
            headers["Authorization"] = entry.value
            continue
        if (
            resolved_provider_type == ProviderType.MAAS
            and normalized_name in reserved_maas_headers
        ):
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
