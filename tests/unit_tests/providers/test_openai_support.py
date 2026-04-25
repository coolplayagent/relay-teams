# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import httpx
import pytest

from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    ModelEndpointConfig,
    ProviderType,
)
from relay_teams.providers.openai_compatible import OpenAICompatibleProvider
from relay_teams.providers.openai_support import build_openai_provider_for_endpoint


class _CapturedProvider:
    def __init__(self, *, openai_client: object) -> None:
        self.openai_client = openai_client


def test_build_openai_provider_for_codeagent_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.providers.openai_support.OpenAIProvider",
        _CapturedProvider,
    )
    client = httpx.AsyncClient(trust_env=False)

    try:
        with pytest.raises(
            ValueError,
            match="CodeAgent provider requires codeagent_auth configuration.",
        ):
            build_openai_provider_for_endpoint(
                base_url="https://codeagent.example/codeAgentPro",
                api_key=None,
                headers=(),
                http_client=client,
                provider_type=ProviderType.CODEAGENT,
            )
    finally:
        asyncio.run(client.aclose())


def test_build_openai_provider_for_codeagent_uses_codeagent_client(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    client = httpx.AsyncClient(trust_env=False)

    monkeypatch.setattr(
        "relay_teams.providers.openai_support.OpenAIProvider",
        _CapturedProvider,
    )
    monkeypatch.setattr(
        "relay_teams.providers.openai_support.build_codeagent_openai_client",
        lambda **kwargs: captured.update(kwargs) or "codeagent-client",
    )

    try:
        provider = build_openai_provider_for_endpoint(
            base_url="https://codeagent.example/codeAgentPro",
            api_key=None,
            headers=(),
            http_client=client,
            provider_type=ProviderType.CODEAGENT,
            codeagent_auth=CodeAgentAuthConfig(refresh_token="codeagent-refresh-token"),
            ssl_verify=False,
            connect_timeout_seconds=22.0,
        )
    finally:
        asyncio.run(client.aclose())

    assert isinstance(provider, _CapturedProvider)
    assert provider.openai_client == "codeagent-client"
    assert captured["base_url"] == "https://codeagent.example/codeAgentPro"
    assert captured["connect_timeout_seconds"] == 22.0
    assert captured["ssl_verify"] is False


def test_openai_compatible_provider_capabilities_are_empty_for_codeagent() -> None:
    provider = object.__new__(OpenAICompatibleProvider)
    provider._config_ref = ModelEndpointConfig(
        provider=ProviderType.CODEAGENT,
        model="codeagent-chat",
        base_url="https://codeagent.example/codeAgentPro",
        codeagent_auth=CodeAgentAuthConfig(refresh_token="codeagent-refresh-token"),
    )

    capabilities = provider.capabilities()

    assert capabilities.input_modalities == ()
    assert capabilities.conversation_output_modalities == ()
