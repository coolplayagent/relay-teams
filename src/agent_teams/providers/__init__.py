# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.providers.provider_contracts import EchoProvider, LLMProvider
    from agent_teams.providers.model_config import (
        LlmRetryConfig,
        ModelEndpointConfig,
        ModelRequestHeader,
        ProviderModelInfo,
        ProviderType,
        SamplingConfig,
    )
    from agent_teams.providers.llm_retry import (
        LlmRetryErrorInfo,
        LlmRetrySchedule,
        compute_retry_delay_ms,
        extract_retry_error_info,
        run_with_llm_retry,
    )
    from agent_teams.providers.openai_compatible import OpenAICompatibleProvider
    from agent_teams.providers.model_config_manager import ModelConfigManager
    from agent_teams.providers.model_config_service import ModelConfigService
    from agent_teams.providers.model_connectivity import (
        ModelDiscoveryEntry,
        ModelConnectivityDiagnostics,
        ModelConnectivityProbeOverride,
        ModelConnectivityProbeRequest,
        ModelConnectivityProbeResult,
        ModelDiscoveryResult,
        ModelConnectivityProbeService,
        ModelConnectivityTokenUsage,
    )
    from agent_teams.providers.known_model_context_windows import (
        infer_known_context_window,
    )
    from agent_teams.providers.provider_registry import (
        ProviderRegistry,
        create_default_provider_registry,
        list_provider_models,
    )
    from agent_teams.providers.token_usage_repo import (
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
    "LlmRetryErrorInfo",
    "LlmRetrySchedule",
    "ModelEndpointConfig",
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
        "agent_teams.providers.token_usage_repo",
        "AgentTokenSummary",
    ),
    "EchoProvider": ("agent_teams.providers.provider_contracts", "EchoProvider"),
    "LLMProvider": ("agent_teams.providers.provider_contracts", "LLMProvider"),
    "LlmRetryConfig": ("agent_teams.providers.model_config", "LlmRetryConfig"),
    "LlmRetryErrorInfo": (
        "agent_teams.providers.llm_retry",
        "LlmRetryErrorInfo",
    ),
    "LlmRetrySchedule": ("agent_teams.providers.llm_retry", "LlmRetrySchedule"),
    "ModelEndpointConfig": (
        "agent_teams.providers.model_config",
        "ModelEndpointConfig",
    ),
    "ModelRequestHeader": (
        "agent_teams.providers.model_config",
        "ModelRequestHeader",
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
    "ModelDiscoveryEntry": (
        "agent_teams.providers.model_connectivity",
        "ModelDiscoveryEntry",
    ),
    "ModelDiscoveryResult": (
        "agent_teams.providers.model_connectivity",
        "ModelDiscoveryResult",
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
    "ProviderRegistry": ("agent_teams.providers.provider_registry", "ProviderRegistry"),
    "ProviderType": ("agent_teams.providers.model_config", "ProviderType"),
    "RunTokenUsage": (
        "agent_teams.providers.token_usage_repo",
        "RunTokenUsage",
    ),
    "SamplingConfig": ("agent_teams.providers.model_config", "SamplingConfig"),
    "SessionTokenUsage": (
        "agent_teams.providers.token_usage_repo",
        "SessionTokenUsage",
    ),
    "TokenUsageRecord": (
        "agent_teams.providers.token_usage_repo",
        "TokenUsageRecord",
    ),
    "TokenUsageRepository": (
        "agent_teams.providers.token_usage_repo",
        "TokenUsageRepository",
    ),
    "compute_retry_delay_ms": (
        "agent_teams.providers.llm_retry",
        "compute_retry_delay_ms",
    ),
    "create_default_provider_registry": (
        "agent_teams.providers.provider_registry",
        "create_default_provider_registry",
    ),
    "extract_retry_error_info": (
        "agent_teams.providers.llm_retry",
        "extract_retry_error_info",
    ),
    "infer_known_context_window": (
        "agent_teams.providers.known_model_context_windows",
        "infer_known_context_window",
    ),
    "list_provider_models": (
        "agent_teams.providers.provider_registry",
        "list_provider_models",
    ),
    "run_with_llm_retry": ("agent_teams.providers.llm_retry", "run_with_llm_retry"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
