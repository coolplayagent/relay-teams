# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.providers.http_client_factory import (
        ProxyEnvConfig,
        build_llm_http_client,
    )
    from agent_teams.providers.contracts import EchoProvider, LLMProvider
    from agent_teams.providers.model_config import (
        ModelEndpointConfig,
        ProviderModelInfo,
        ProviderType,
        SamplingConfig,
    )
    from agent_teams.providers.openai_compatible import OpenAICompatibleProvider
    from agent_teams.providers.model_config_manager import ModelConfigManager
    from agent_teams.providers.model_config_service import ModelConfigService
    from agent_teams.providers.model_connectivity import (
        ModelConnectivityDiagnostics,
        ModelConnectivityProbeOverride,
        ModelConnectivityProbeRequest,
        ModelConnectivityProbeResult,
        ModelConnectivityProbeService,
        ModelConnectivityTokenUsage,
    )
    from agent_teams.providers.registry import (
        ProviderRegistry,
        create_default_provider_registry,
        list_provider_models,
    )

__all__ = [
    "EchoProvider",
    "LLMProvider",
    "ModelEndpointConfig",
    "ModelConfigManager",
    "ModelConfigService",
    "ModelConnectivityDiagnostics",
    "ModelConnectivityProbeOverride",
    "ModelConnectivityProbeRequest",
    "ModelConnectivityProbeResult",
    "ModelConnectivityProbeService",
    "ModelConnectivityTokenUsage",
    "OpenAICompatibleProvider",
    "ProviderModelInfo",
    "ProviderRegistry",
    "ProviderType",
    "ProxyEnvConfig",
    "SamplingConfig",
    "build_llm_http_client",
    "create_default_provider_registry",
    "list_provider_models",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "EchoProvider": ("agent_teams.providers.contracts", "EchoProvider"),
    "LLMProvider": ("agent_teams.providers.contracts", "LLMProvider"),
    "ModelEndpointConfig": (
        "agent_teams.providers.model_config",
        "ModelEndpointConfig",
    ),
    "ModelConfigManager": (
        "agent_teams.providers.model_config_manager",
        "ModelConfigManager",
    ),
    "ModelConfigService": (
        "agent_teams.providers.model_config_service",
        "ModelConfigService",
    ),
    "ModelConnectivityDiagnostics": (
        "agent_teams.providers.model_connectivity",
        "ModelConnectivityDiagnostics",
    ),
    "ModelConnectivityProbeOverride": (
        "agent_teams.providers.model_connectivity",
        "ModelConnectivityProbeOverride",
    ),
    "ModelConnectivityProbeRequest": (
        "agent_teams.providers.model_connectivity",
        "ModelConnectivityProbeRequest",
    ),
    "ModelConnectivityProbeResult": (
        "agent_teams.providers.model_connectivity",
        "ModelConnectivityProbeResult",
    ),
    "ModelConnectivityProbeService": (
        "agent_teams.providers.model_connectivity",
        "ModelConnectivityProbeService",
    ),
    "ModelConnectivityTokenUsage": (
        "agent_teams.providers.model_connectivity",
        "ModelConnectivityTokenUsage",
    ),
    "OpenAICompatibleProvider": (
        "agent_teams.providers.openai_compatible",
        "OpenAICompatibleProvider",
    ),
    "ProviderModelInfo": (
        "agent_teams.providers.model_config",
        "ProviderModelInfo",
    ),
    "ProviderRegistry": ("agent_teams.providers.registry", "ProviderRegistry"),
    "ProviderType": ("agent_teams.providers.model_config", "ProviderType"),
    "ProxyEnvConfig": (
        "agent_teams.providers.http_client_factory",
        "ProxyEnvConfig",
    ),
    "SamplingConfig": ("agent_teams.providers.model_config", "SamplingConfig"),
    "build_llm_http_client": (
        "agent_teams.providers.http_client_factory",
        "build_llm_http_client",
    ),
    "create_default_provider_registry": (
        "agent_teams.providers.registry",
        "create_default_provider_registry",
    ),
    "list_provider_models": (
        "agent_teams.providers.registry",
        "list_provider_models",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
