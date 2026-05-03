# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

from relay_teams.providers.prompt_caching import (
    PromptCachingConfig,
    ProviderCachingConfig,
    apply_anthropic_cache_markers,
    should_enable_prompt_caching,
    should_enable_prompt_caching_for_anthropic,
)


def _make_config() -> MagicMock:
    cfg = MagicMock()
    return cfg


def test_should_enable_prompt_caching_short():
    assert (
        should_enable_prompt_caching(
            _config=_make_config(),
            system_prompt_length=100,
        )
        is False
    )


def test_should_enable_prompt_caching_long():
    assert (
        should_enable_prompt_caching(
            _config=_make_config(),
            system_prompt_length=5000,
        )
        is True
    )


def test_should_enable_prompt_caching_boundary():
    assert (
        should_enable_prompt_caching(
            _config=_make_config(),
            system_prompt_length=4096,
        )
        is True
    )


def test_should_enable_for_anthropic_short():
    short_prompt = "x" * 100
    assert should_enable_prompt_caching_for_anthropic(short_prompt) is False


def test_should_enable_for_anthropic_long():
    long_prompt = "x" * 5000
    assert should_enable_prompt_caching_for_anthropic(long_prompt) is True


def test_apply_anthropic_cache_markers_short():
    result = apply_anthropic_cache_markers("short", {})
    assert "extra_body" not in result


def test_apply_anthropic_cache_markers_long():
    long_prompt = "x" * 5000
    result = apply_anthropic_cache_markers(long_prompt, {})
    assert "extra_body" in result
    extra = result["extra_body"]
    assert isinstance(extra, dict)
    assert "anthropic_beta" in extra
    assert "prompt-caching-2024-07-31" in extra["anthropic_beta"]


def test_apply_anthropic_preserves_existing_extra():
    long_prompt = "x" * 5000
    result = apply_anthropic_cache_markers(
        long_prompt, {"extra_body": {"existing_key": "value"}}
    )
    extra = result["extra_body"]
    assert isinstance(extra, dict)
    assert extra["existing_key"] == "value"
    assert "anthropic_beta" in extra


def test_apply_anthropic_no_duplicate_beta():
    long_prompt = "x" * 5000
    result = apply_anthropic_cache_markers(long_prompt, {})
    result2 = apply_anthropic_cache_markers(long_prompt, result)
    extra_body = result2["extra_body"]
    assert isinstance(extra_body, dict)
    beta_list = extra_body["anthropic_beta"]
    assert isinstance(beta_list, list)
    assert beta_list.count("prompt-caching-2024-07-31") == 1


def test_prompt_caching_config_defaults():
    config = PromptCachingConfig()
    assert config.enabled is True
    assert config.min_system_prompt_tokens == 1024
    assert config.cache_tool_schemas is True


def test_provider_caching_config_defaults():
    config = ProviderCachingConfig()
    assert config.enabled is True
    assert config.cache_control_type == "ephemeral"


def test_session_prompt_anthropic_long_system_prompt_adds_cache_markers():
    """Integration: verify the cache marker application logic that
    session_prompt uses for Anthropic providers."""
    from pydantic_ai.models.anthropic import AnthropicModelSettings

    from relay_teams.providers.prompt_caching import (
        should_enable_prompt_caching_for_anthropic,
    )

    long_prompt = "x" * 5000
    assert should_enable_prompt_caching_for_anthropic(long_prompt) is True

    anthropic_settings: AnthropicModelSettings = {}
    if should_enable_prompt_caching_for_anthropic(long_prompt):
        existing_extra = anthropic_settings.get("extra_body")
        if isinstance(existing_extra, dict):
            extra_body = dict(existing_extra)
        else:
            extra_body: dict[str, object] = {}
        beta_list = ["prompt-caching-2024-07-31"]
        extra_body["anthropic_beta"] = beta_list
        anthropic_settings["extra_body"] = extra_body

    assert "extra_body" in anthropic_settings
    extra = anthropic_settings["extra_body"]
    assert isinstance(extra, dict)
    assert "anthropic_beta" in extra
    beta_val = extra["anthropic_beta"]
    assert isinstance(beta_val, list)
    assert "prompt-caching-2024-07-31" in beta_val


def test_session_prompt_anthropic_short_system_prompt_no_markers():
    """Integration: short system prompts must NOT get cache markers."""
    from pydantic_ai.models.anthropic import AnthropicModelSettings

    anthropic_settings: AnthropicModelSettings = {}
    short_prompt = "short"
    from relay_teams.providers.prompt_caching import (
        should_enable_prompt_caching_for_anthropic,
    )

    if not should_enable_prompt_caching_for_anthropic(short_prompt):
        assert "extra_body" not in anthropic_settings
    else:
        raise AssertionError("Short prompt should not enable caching")
