# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from agent_teams.providers.provider_contracts import EchoProvider, LLMProvider
from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderModelInfo,
    ProviderType,
)

ProviderBuilder = Callable[[ModelEndpointConfig], LLMProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._builders: dict[ProviderType, ProviderBuilder] = {}

    def register(self, provider_type: ProviderType, builder: ProviderBuilder) -> None:
        self._builders[provider_type] = builder

    def create(self, config: ModelEndpointConfig) -> LLMProvider:
        builder = self._builders.get(config.provider)
        if builder is None:
            raise ValueError(f"Provider '{config.provider}' is not registered")
        return builder(config)


def list_provider_models(
    profiles: dict[str, ModelEndpointConfig],
    provider: ProviderType | None = None,
) -> tuple[ProviderModelInfo, ...]:
    entries: list[ProviderModelInfo] = []
    for profile_name, config in profiles.items():
        if provider is not None and config.provider != provider:
            continue
        entries.append(
            ProviderModelInfo(
                profile=profile_name,
                provider=config.provider,
                model=config.model,
                base_url=config.base_url,
            )
        )
    entries.sort(key=lambda item: (item.provider.value, item.profile, item.model))
    return tuple(entries)


def create_default_provider_registry(
    *,
    openai_compatible_builder: ProviderBuilder,
) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(ProviderType.OPENAI_COMPATIBLE, openai_compatible_builder)
    registry.register(ProviderType.BIGMODEL, openai_compatible_builder)
    registry.register(ProviderType.MINIMAX, openai_compatible_builder)
    registry.register(ProviderType.ECHO, lambda _config: EchoProvider())
    return registry
