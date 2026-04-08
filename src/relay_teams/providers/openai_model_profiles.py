# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai.profiles.openai import OpenAIModelProfile


_DEEPSEEK_THINKING_MODEL_MARKERS = (
    "deepseek-r1",
    "deepseek-reasoner",
)


def resolve_openai_chat_model_profile(
    *,
    base_url: str,
    model_name: str,
) -> OpenAIModelProfile | None:
    normalized_model = model_name.strip().lower()
    normalized_base_url = base_url.strip().lower()
    if any(marker in normalized_model for marker in _DEEPSEEK_THINKING_MODEL_MARKERS):
        return OpenAIModelProfile(thinking_tags=("<think>", "</think>"))
    if "deepseek" in normalized_base_url and normalized_model:
        return OpenAIModelProfile(thinking_tags=("<think>", "</think>"))
    return None
