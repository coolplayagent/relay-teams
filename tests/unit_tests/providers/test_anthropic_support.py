# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from anthropic import AsyncAnthropic
import httpx

from relay_teams.providers.anthropic_support import (
    anthropic_api_endpoint,
    build_anthropic_model,
    build_anthropic_provider,
    build_anthropic_request_headers,
    normalize_anthropic_sdk_base_url,
)
from relay_teams.providers.model_config import (
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
)


def test_normalize_anthropic_sdk_base_url_strips_v1_suffix() -> None:
    assert normalize_anthropic_sdk_base_url(" ") == "https://api.anthropic.com"
    assert (
        anthropic_api_endpoint(" ", "models") == "https://api.anthropic.com/v1/models"
    )
    assert (
        normalize_anthropic_sdk_base_url("https://api.minimax.io/anthropic/v1")
        == "https://api.minimax.io/anthropic"
    )
    assert (
        anthropic_api_endpoint("https://api.minimax.io/anthropic/v1", "messages")
        == "https://api.minimax.io/anthropic/v1/messages"
    )
    assert (
        anthropic_api_endpoint("https://api.anthropic.com", "models")
        == "https://api.anthropic.com/v1/models"
    )


def test_build_anthropic_request_headers_uses_x_api_key() -> None:
    headers = build_anthropic_request_headers(
        ModelEndpointConfig(
            provider=ProviderType.ANTHROPIC,
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="anthropic-key",
            headers=(
                ModelRequestHeader(name="anthropic-version", value="custom-version"),
            ),
        ),
        extra_headers={"Content-Type": "application/json"},
    )

    assert headers["x-api-key"] == "anthropic-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["Content-Type"] == "application/json"


def test_build_anthropic_provider_accepts_header_only_auth() -> None:
    client = httpx.AsyncClient(trust_env=False)

    try:
        provider = build_anthropic_provider(
            config=ModelEndpointConfig(
                provider=ProviderType.ANTHROPIC,
                model="claude-sonnet-4-5",
                base_url="https://api.anthropic.com",
                headers=(ModelRequestHeader(name="Authorization", value="Bearer key"),),
            ),
            http_client=client,
        )
    finally:
        asyncio.run(client.aclose())

    anthropic_client = provider.client
    assert isinstance(anthropic_client, AsyncAnthropic)
    assert anthropic_client.api_key is None
    assert anthropic_client.auth_headers == {}
    assert anthropic_client.default_headers["Authorization"] == "Bearer key"


def test_build_anthropic_provider_accepts_x_api_key_header() -> None:
    client = httpx.AsyncClient(trust_env=False)

    try:
        provider = build_anthropic_provider(
            config=ModelEndpointConfig(
                provider=ProviderType.ANTHROPIC,
                model="MiniMax-M2.7",
                base_url="https://api.minimax.io/anthropic/v1",
                headers=(ModelRequestHeader(name="x-api-key", value="minimax-key"),),
            ),
            http_client=client,
        )
    finally:
        asyncio.run(client.aclose())

    assert str(provider.client.base_url) == "https://api.minimax.io/anthropic/"


def test_build_anthropic_model_uses_custom_model_id() -> None:
    client = httpx.AsyncClient(trust_env=False)

    try:
        model = build_anthropic_model(
            config=ModelEndpointConfig(
                provider=ProviderType.ANTHROPIC,
                model="MiniMax-M2.7",
                base_url="https://api.minimax.io/anthropic/v1",
                api_key="minimax-key",
            ),
            http_client=client,
        )
    finally:
        asyncio.run(client.aclose())

    assert model.model_name == "MiniMax-M2.7"
