# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.providers.provider_contracts import EchoProvider, LLMProvider
    from relay_teams.providers.model_config import (
        LlmRetryConfig,
        MaaSAuthConfig,
        ModelConfigPayload,
        ModelEndpointConfig,
        ModelProfileConfigPayload,
        ModelRequestHeader,
        ProviderModelInfo,
        ProviderType,
        SamplingConfig,
    )
    from relay_teams.providers.llm_retry import (
        LlmRetryErrorInfo,
        LlmRetrySchedule,
        compute_retry_delay_ms,
        extract_retry_error_info,
        run_with_llm_retry,
    )
    from relay_teams.providers.openai_compatible import OpenAICompatibleProvider
    from relay_teams.providers.model_config_manager import ModelConfigManager
    from relay_teams.providers.model_config_service import ModelConfigService
    from relay_teams.providers.model_connectivity import (
        ModelDiscoveryEntry,
        ModelConnectivityDiagnostics,
        ModelConnectivityProbeOverride,
        ModelConnectivityProbeRequest,
        ModelConnectivityProbeResult,
        ModelDiscoveryResult,
        ModelConnectivityProbeService,
        ModelConnectivityTokenUsage,
    )
    from relay_teams.providers.known_model_context_windows import (
        infer_known_context_window,
    )
    from relay_teams.providers.provider_registry import (
        ProviderRegistry,
        create_default_provider_registry,
        list_provider_models,
    )
    from relay_teams.providers.token_usage_repo import (
        AgentTokenSummary,
        RunTokenUsage,
        SessionTokenUsage,
        TokenUsageRecord,
        TokenUsageRepository,
    )

__all__ = [
    "AgentTokenSummary",
    "EchoProvider",
    "LLMProvider",
    "LlmRetryConfig",
    "MaaSAuthConfig",
    "ModelConfigPayload",
    "LlmRetryErrorInfo",
    "LlmRetrySchedule",
    "ModelEndpointConfig",
    "ModelProfileConfigPayload",
    "ModelRequestHeader",
    "ModelConfigManager",
    "ModelConfigService",
    "ModelDiscoveryEntry",
    "ModelDiscoveryResult",
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
    "RunTokenUsage",
    "SamplingConfig",
    "SessionTokenUsage",
    "TokenUsageRecord",
    "TokenUsageRepository",
    "compute_retry_delay_ms",
    "create_default_provider_registry",
    "extract_retry_error_info",
    "infer_known_context_window",
    "list_provider_models",
    "run_with_llm_retry",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AgentTokenSummary": (
        "relay_teams.providers.token_usage_repo",
        "AgentTokenSummary",
    ),
    "EchoProvider": ("relay_teams.providers.provider_contracts", "EchoProvider"),
    "LLMProvider": ("relay_teams.providers.provider_contracts", "LLMProvider"),
    "LlmRetryConfig": ("relay_teams.providers.model_config", "LlmRetryConfig"),
    "MaaSAuthConfig": ("relay_teams.providers.model_config", "MaaSAuthConfig"),
    "ModelConfigPayload": ("relay_teams.providers.model_config", "ModelConfigPayload"),
    "LlmRetryErrorInfo": (
        "relay_teams.providers.llm_retry",
        "LlmRetryErrorInfo",
    ),
    "LlmRetrySchedule": ("relay_teams.providers.llm_retry", "LlmRetrySchedule"),
    "ModelEndpointConfig": (
        "relay_teams.providers.model_config",
        "ModelEndpointConfig",
    ),
    "ModelProfileConfigPayload": (
        "relay_teams.providers.model_config",
        "ModelProfileConfigPayload",
    ),
    "ModelRequestHeader": (
        "relay_teams.providers.model_config",
        "ModelRequestHeader",
    ),
    "ModelConfigManager": (
        "relay_teams.providers.model_config_manager",
        "ModelConfigManager",
    ),
    "ModelConfigService": (
        "relay_teams.providers.model_config_service",
        "ModelConfigService",
    ),
    "ModelConnectivityDiagnostics": (
        "relay_teams.providers.model_connectivity",
        "ModelConnectivityDiagnostics",
    ),
    "ModelDiscoveryEntry": (
        "relay_teams.providers.model_connectivity",
        "ModelDiscoveryEntry",
    ),
    "ModelDiscoveryResult": (
        "relay_teams.providers.model_connectivity",
        "ModelDiscoveryResult",
    ),
    "ModelConnectivityProbeOverride": (
        "relay_teams.providers.model_connectivity",
        "ModelConnectivityProbeOverride",
    ),
    "ModelConnectivityProbeRequest": (
        "relay_teams.providers.model_connectivity",
        "ModelConnectivityProbeRequest",
    ),
    "ModelConnectivityProbeResult": (
        "relay_teams.providers.model_connectivity",
        "ModelConnectivityProbeResult",
    ),
    "ModelConnectivityProbeService": (
        "relay_teams.providers.model_connectivity",
        "ModelConnectivityProbeService",
    ),
    "ModelConnectivityTokenUsage": (
        "relay_teams.providers.model_connectivity",
        "ModelConnectivityTokenUsage",
    ),
    "OpenAICompatibleProvider": (
        "relay_teams.providers.openai_compatible",
        "OpenAICompatibleProvider",
    ),
    "ProviderModelInfo": (
        "relay_teams.providers.model_config",
        "ProviderModelInfo",
    ),
    "ProviderRegistry": ("relay_teams.providers.provider_registry", "ProviderRegistry"),
    "ProviderType": ("relay_teams.providers.model_config", "ProviderType"),
    "RunTokenUsage": (
        "relay_teams.providers.token_usage_repo",
        "RunTokenUsage",
    ),
    "SamplingConfig": ("relay_teams.providers.model_config", "SamplingConfig"),
    "SessionTokenUsage": (
        "relay_teams.providers.token_usage_repo",
        "SessionTokenUsage",
    ),
    "TokenUsageRecord": (
        "relay_teams.providers.token_usage_repo",
        "TokenUsageRecord",
    ),
    "TokenUsageRepository": (
        "relay_teams.providers.token_usage_repo",
        "TokenUsageRepository",
    ),
    "compute_retry_delay_ms": (
        "relay_teams.providers.llm_retry",
        "compute_retry_delay_ms",
    ),
    "create_default_provider_registry": (
        "relay_teams.providers.provider_registry",
        "create_default_provider_registry",
    ),
    "extract_retry_error_info": (
        "relay_teams.providers.llm_retry",
        "extract_retry_error_info",
    ),
    "infer_known_context_window": (
        "relay_teams.providers.known_model_context_windows",
        "infer_known_context_window",
    ),
    "list_provider_models": (
        "relay_teams.providers.provider_registry",
        "list_provider_models",
    ),
    "run_with_llm_retry": ("relay_teams.providers.llm_retry", "run_with_llm_retry"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
