# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from relay_teams.providers.model_config import (
    LlmRetryConfig,
    MaaSAuthConfig,
    ModelCapabilities,
    ModelConfigPayload,
    ModelEndpointConfig,
    ModelFallbackConfig,
    ModelFallbackPolicy,
    ModelFallbackStrategy,
    ModelFallbackTrigger,
    ModelModalityMatrix,
    ModelProfileConfigPayload,
    ModelRequestHeader,
    ProviderModelInfo,
    ProviderType,
    SamplingConfig,
    default_model_fallback_config,
)

if TYPE_CHECKING:
    from relay_teams.providers.provider_contracts import EchoProvider, LLMProvider
    from relay_teams.providers.model_config import (
        CodeAgentAuthConfig,
        LlmRetryConfig,
        MaaSAuthConfig,
        ModelCapabilities,
        ModelConfigPayload,
        ModelEndpointConfig,
        ModelFallbackConfig,
        ModelFallbackPolicy,
        ModelFallbackStrategy,
        ModelFallbackTrigger,
        ModelProfileConfigPayload,
        ModelModalityMatrix,
        ModelRequestHeader,
        ProviderModelInfo,
        ProviderType,
        SamplingConfig,
        default_model_fallback_config,
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
    from relay_teams.providers.model_fallback import (
        DisabledLlmFallbackMiddleware,
        LlmFallbackDecision,
        LlmFallbackMiddleware,
        ProfileCooldownRecord,
        ProfileCooldownRegistry,
    )
    from relay_teams.providers.model_fallback_config_manager import (
        ModelFallbackConfigManager,
    )
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
    "CodeAgentAuthConfig",
    "EchoProvider",
    "LLMProvider",
    "LlmRetryConfig",
    "MaaSAuthConfig",
    "ModelCapabilities",
    "ModelConfigPayload",
    "ModelFallbackConfig",
    "ModelFallbackPolicy",
    "ModelFallbackStrategy",
    "ModelFallbackTrigger",
    "ModelEndpointConfig",
    "ModelProfileConfigPayload",
    "ModelModalityMatrix",
    "ModelRequestHeader",
    "ProviderModelInfo",
    "ProviderType",
    "RunTokenUsage",
    "SamplingConfig",
    "SessionTokenUsage",
    "TokenUsageRecord",
    "TokenUsageRepository",
    "default_model_fallback_config",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AgentTokenSummary": (
        "relay_teams.providers.token_usage_repo",
        "AgentTokenSummary",
    ),
    "EchoProvider": ("relay_teams.providers.provider_contracts", "EchoProvider"),
    "LLMProvider": ("relay_teams.providers.provider_contracts", "LLMProvider"),
    "LlmRetryConfig": ("relay_teams.providers.model_config", "LlmRetryConfig"),
    "CodeAgentAuthConfig": (
        "relay_teams.providers.model_config",
        "CodeAgentAuthConfig",
    ),
    "MaaSAuthConfig": ("relay_teams.providers.model_config", "MaaSAuthConfig"),
    "ModelCapabilities": (
        "relay_teams.providers.model_config",
        "ModelCapabilities",
    ),
    "ModelConfigPayload": ("relay_teams.providers.model_config", "ModelConfigPayload"),
    "ModelFallbackConfig": (
        "relay_teams.providers.model_config",
        "ModelFallbackConfig",
    ),
    "ModelFallbackPolicy": (
        "relay_teams.providers.model_config",
        "ModelFallbackPolicy",
    ),
    "ModelFallbackStrategy": (
        "relay_teams.providers.model_config",
        "ModelFallbackStrategy",
    ),
    "ModelFallbackTrigger": (
        "relay_teams.providers.model_config",
        "ModelFallbackTrigger",
    ),
    "LlmRetryErrorInfo": (
        "relay_teams.providers.llm_retry",
        "LlmRetryErrorInfo",
    ),
    "LlmRetrySchedule": ("relay_teams.providers.llm_retry", "LlmRetrySchedule"),
    "LlmFallbackDecision": (
        "relay_teams.providers.model_fallback",
        "LlmFallbackDecision",
    ),
    "LlmFallbackMiddleware": (
        "relay_teams.providers.model_fallback",
        "LlmFallbackMiddleware",
    ),
    "ModelEndpointConfig": (
        "relay_teams.providers.model_config",
        "ModelEndpointConfig",
    ),
    "ModelProfileConfigPayload": (
        "relay_teams.providers.model_config",
        "ModelProfileConfigPayload",
    ),
    "ModelModalityMatrix": (
        "relay_teams.providers.model_config",
        "ModelModalityMatrix",
    ),
    "ModelRequestHeader": (
        "relay_teams.providers.model_config",
        "ModelRequestHeader",
    ),
    "ModelConfigManager": (
        "relay_teams.providers.model_config_manager",
        "ModelConfigManager",
    ),
    "ModelFallbackConfigManager": (
        "relay_teams.providers.model_fallback_config_manager",
        "ModelFallbackConfigManager",
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
    "ProfileCooldownRecord": (
        "relay_teams.providers.model_fallback",
        "ProfileCooldownRecord",
    ),
    "ProfileCooldownRegistry": (
        "relay_teams.providers.model_fallback",
        "ProfileCooldownRegistry",
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
    "default_model_fallback_config": (
        "relay_teams.providers.model_config",
        "default_model_fallback_config",
    ),
    "DisabledLlmFallbackMiddleware": (
        "relay_teams.providers.model_fallback",
        "DisabledLlmFallbackMiddleware",
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
