# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.settings import ModelSettings

from relay_teams.agents.execution.recoverable_openai_chat_model import (
    RecoverableOpenAIChatModel,
)
from relay_teams.providers.anthropic_support import build_anthropic_model
from relay_teams.providers.model_config import ModelEndpointConfig, ProviderType
from relay_teams.providers.openai_model_profiles import (
    resolve_openai_chat_model_profile,
)
from relay_teams.providers.openai_support import build_openai_provider

RuntimeChatModel = OpenAIChatModel | RecoverableOpenAIChatModel | AnthropicModel


def is_anthropic_provider(provider: ProviderType) -> bool:
    return provider == ProviderType.ANTHROPIC


def build_runtime_chat_model(
    *,
    config: ModelEndpointConfig,
    http_client: httpx.AsyncClient,
    recoverable_openai: bool = False,
) -> RuntimeChatModel:
    if is_anthropic_provider(config.provider):
        return build_anthropic_model(config=config, http_client=http_client)

    profile: OpenAIModelProfile | None = resolve_openai_chat_model_profile(
        base_url=config.base_url,
        model_name=config.model,
    )
    model_cls = RecoverableOpenAIChatModel if recoverable_openai else OpenAIChatModel
    return model_cls(
        config.model,
        provider=build_openai_provider(
            config=config,
            http_client=http_client,
        ),
        profile=profile,
    )


def build_base_model_settings(config: ModelEndpointConfig) -> ModelSettings:
    if is_anthropic_provider(config.provider):
        settings: AnthropicModelSettings = {}
        if config.sampling.max_tokens is not None:
            settings["max_tokens"] = config.sampling.max_tokens
        return settings
    openai_settings: OpenAIChatModelSettings = {
        "openai_continuous_usage_stats": True,
        "temperature": config.sampling.temperature,
        "top_p": config.sampling.top_p,
    }
    if config.sampling.max_tokens is not None:
        openai_settings["max_tokens"] = config.sampling.max_tokens
    return openai_settings
