# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.providers.model_config import ModelEndpointConfig


class ProviderCachingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    cache_control_type: str = "ephemeral"


class PromptCachingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min_system_prompt_tokens: int = 1024
    cache_tool_schemas: bool = True
    provider_overrides: dict[str, ProviderCachingConfig] = Field(default_factory=dict)


def should_enable_prompt_caching(
    _config: ModelEndpointConfig,
    system_prompt_length: int,
) -> bool:
    """Determine whether prompt caching should be enabled.

    Returns True when the system prompt is long enough to benefit
    from caching (>= 1024 estimated tokens / ~4096 bytes).
    """
    min_bytes = 1024 * 4
    if system_prompt_length < min_bytes:
        return False
    return True


def should_enable_prompt_caching_for_anthropic(
    system_prompt: str,
) -> bool:
    """Check whether an Anthropic system prompt is long enough for caching."""
    estimated_tokens = len(system_prompt) // 4
    return estimated_tokens >= 1024


def apply_anthropic_cache_markers(
    system_prompt: str,
    model_settings: dict[str, object],
) -> dict[str, object]:
    """Apply Anthropic prompt caching markers to model settings.

    Adds the ``anthropic_beta`` header and sets up ``extra_body``
    with cache control metadata when the system prompt is long
    enough to benefit from caching.
    """
    updated = dict(model_settings)
    if not should_enable_prompt_caching_for_anthropic(system_prompt):
        return updated

    existing_extra = updated.get("extra_body")
    extra_body: dict[str, object]
    if isinstance(existing_extra, dict):
        extra_body = dict(existing_extra)
    else:
        extra_body = {}

    beta_list = extra_body.get("anthropic_beta")
    if isinstance(beta_list, list):
        if "prompt-caching-2024-07-31" not in beta_list:
            beta_list.append("prompt-caching-2024-07-31")
    else:
        beta_list = ["prompt-caching-2024-07-31"]
    extra_body["anthropic_beta"] = beta_list
    updated["extra_body"] = extra_body
    return updated


# ---------------------------------------------------------------------------
# EP-1: OpenAI prompt caching support
# ---------------------------------------------------------------------------

_OPENAI_CACHEABLE_MODEL_PREFIXES = (
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4-0",
    "chatgpt-4o",
    "o1-",
    "o3-",
    "o4-",
)


def should_enable_prompt_caching_for_openai(
    model: str,
    system_prompt: str,
) -> bool:
    """Check whether an OpenAI model supports prompt caching.

    OpenAI prompt caching (formerly called 'cached responses') is
    available on GPT-4o-class and newer models when the system prompt
    is at least ~1024 tokens.
    """
    model_lower = model.lower().strip()
    is_cacheable = any(
        model_lower.startswith(prefix) for prefix in _OPENAI_CACHEABLE_MODEL_PREFIXES
    )
    if not is_cacheable:
        return False
    estimated_tokens = len(system_prompt) // 4
    return estimated_tokens >= 1024


def apply_openai_cache_markers(
    model: str,
    system_prompt: str,
    model_settings: dict[str, object],
) -> dict[str, object]:
    """Apply OpenAI prompt caching hints to model settings.

    When model caching is supported, this sets ``store=True`` in the
    request ``extra_body`` so the API can reuse cached prompt prefixes.
    """
    if not should_enable_prompt_caching_for_openai(model, system_prompt):
        return dict(model_settings)

    updated = dict(model_settings)
    existing_extra = updated.get("extra_body")
    extra_body: dict[str, object]
    if isinstance(existing_extra, dict):
        extra_body = dict(existing_extra)
    else:
        extra_body = {}
    extra_body.setdefault("store", True)
    updated["extra_body"] = extra_body
    return updated
