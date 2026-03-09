# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from agent_teams.providers.llm import (
    EchoProvider,
    LLMProvider,
    OpenAICompatibleProvider,
)
from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderModelInfo,
    ProviderType,
    SamplingConfig,
)
from agent_teams.providers.model_config_manager import ModelConfigManager
from agent_teams.providers.registry import (
    ProviderRegistry,
    create_default_provider_registry,
    list_provider_models,
)

if TYPE_CHECKING:
    from agent_teams.providers.model_config_service import ModelConfigService

__all__ = [
    "EchoProvider",
    "LLMProvider",
    "ModelEndpointConfig",
    "ModelConfigManager",
    "ModelConfigService",
    "OpenAICompatibleProvider",
    "ProviderModelInfo",
    "ProviderRegistry",
    "ProviderType",
    "SamplingConfig",
    "create_default_provider_registry",
    "list_provider_models",
]


def __getattr__(name: str) -> object:
    if name == "ModelConfigService":
        module = importlib.import_module("agent_teams.providers.model_config_service")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
