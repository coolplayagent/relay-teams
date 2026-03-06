# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.providers.llm import EchoProvider
from agent_teams.providers.model_config import ModelEndpointConfig, ProviderType
from agent_teams.providers.registry import (
    ProviderRegistry,
    create_default_provider_registry,
    list_provider_models,
)


def test_provider_registry_creates_registered_provider() -> None:
    registry = ProviderRegistry()
    registry.register(ProviderType.ECHO, lambda _config: EchoProvider())

    provider = registry.create(
        ModelEndpointConfig(
            provider=ProviderType.ECHO,
            model="echo",
            base_url="http://localhost",
            api_key="unused",
        )
    )

    assert isinstance(provider, EchoProvider)


def test_create_default_provider_registry_has_echo_support() -> None:
    registry = create_default_provider_registry(
        openai_compatible_builder=lambda _config: EchoProvider()
    )

    provider = registry.create(
        ModelEndpointConfig(
            provider=ProviderType.ECHO,
            model="echo",
            base_url="http://localhost",
            api_key="unused",
        )
    )

    assert isinstance(provider, EchoProvider)


def test_list_provider_models_can_filter_by_provider() -> None:
    profiles = {
        "default": ModelEndpointConfig(
            provider=ProviderType.OPENAI_COMPATIBLE,
            model="gpt-4o-mini",
            base_url="https://openai-compatible.local/v1",
            api_key="key-openai",
        ),
        "local": ModelEndpointConfig(
            provider=ProviderType.ECHO,
            model="echo",
            base_url="http://localhost",
            api_key="unused",
        ),
    }

    models = list_provider_models(
        profiles=profiles,
        provider=ProviderType.OPENAI_COMPATIBLE,
    )

    assert len(models) == 1
    assert models[0].profile == "default"
    assert models[0].provider == ProviderType.OPENAI_COMPATIBLE
