# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.providers.model_config import ProviderType


def infer_known_context_window(
    *,
    provider: ProviderType,
    model: str,
) -> int | None:
    normalized_model = model.strip().lower()
    if not normalized_model:
        return None

    if provider == ProviderType.OPENAI_COMPATIBLE:
        return _infer_openai_compatible_context_window(normalized_model)
    if provider == ProviderType.BIGMODEL:
        return _infer_bigmodel_context_window(normalized_model)
    return None


def _infer_openai_compatible_context_window(model: str) -> int | None:
    if model.startswith("gpt-4.1"):
        return 1_000_000
    if model.startswith("gpt-4o-mini"):
        return 128_000
    if model.startswith("kimi-k2.5"):
        return 256_000
    return None


def _infer_bigmodel_context_window(model: str) -> int | None:
    if model.startswith("glm-"):
        return 128_000
    return None
