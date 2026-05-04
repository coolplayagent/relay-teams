# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.providers.prompt_caching import (
    apply_openai_cache_markers,
    should_enable_prompt_caching_for_openai,
)


class TestOpenAIPromptCaching:
    def test_caching_enabled_for_gpt4o(self) -> None:
        system_prompt = "x" * 5000
        assert (
            should_enable_prompt_caching_for_openai("gpt-4o-2024-05-13", system_prompt)
            is True
        )

    def test_caching_disabled_for_old_model(self) -> None:
        system_prompt = "x" * 5000
        assert (
            should_enable_prompt_caching_for_openai("gpt-3.5-turbo", system_prompt)
            is False
        )

    def test_caching_disabled_short_prompt(self) -> None:
        system_prompt = "short"
        assert should_enable_prompt_caching_for_openai("gpt-4o", system_prompt) is False

    def test_caching_enabled_for_o1(self) -> None:
        system_prompt = "x" * 5000
        assert (
            should_enable_prompt_caching_for_openai("o1-preview", system_prompt) is True
        )

    def test_caching_enabled_for_o3(self) -> None:
        system_prompt = "x" * 5000
        assert should_enable_prompt_caching_for_openai("o3-mini", system_prompt) is True

    def test_apply_markers_when_eligible(self) -> None:
        system_prompt = "x" * 5000
        settings: dict[str, object] = {}
        result = apply_openai_cache_markers("gpt-4o", system_prompt, settings)
        assert isinstance(result, dict)
        extra_body = result.get("extra_body")
        assert isinstance(extra_body, dict)
        assert extra_body.get("store") is True

    def test_apply_markers_not_eligible(self) -> None:
        system_prompt = "short"
        settings: dict[str, object] = {"existing": True}
        result = apply_openai_cache_markers("gpt-4o", system_prompt, settings)
        assert result == {"existing": True}

    def test_apply_markers_preserves_existing(self) -> None:
        system_prompt = "x" * 5000
        settings: dict[str, object] = {
            "extra_body": {"temperature": 0.7},
        }
        result = apply_openai_cache_markers("gpt-4o", system_prompt, settings)
        extra_body = result.get("extra_body")
        assert isinstance(extra_body, dict)
        assert extra_body.get("temperature") == 0.7
        assert extra_body.get("store") is True
