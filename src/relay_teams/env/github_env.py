# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping

GH_TOKEN_ENV_KEY = "GH_TOKEN"
GITHUB_TOKEN_ENV_KEY = "GITHUB_TOKEN"
GH_PROMPT_DISABLED_ENV_KEY = "GH_PROMPT_DISABLED"
GH_NO_UPDATE_NOTIFIER_ENV_KEY = "GH_NO_UPDATE_NOTIFIER"
GH_NO_EXTENSION_UPDATE_NOTIFIER_ENV_KEY = "GH_NO_EXTENSION_UPDATE_NOTIFIER"
_GITHUB_ENV_KEYS = (GH_TOKEN_ENV_KEY, GITHUB_TOKEN_ENV_KEY)


def normalize_github_token(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value


def resolve_github_token_from_env(env_values: Mapping[str, str]) -> str | None:
    for key in _GITHUB_ENV_KEYS:
        resolved = normalize_github_token(env_values.get(key))
        if resolved is not None:
            return resolved
    return None


def github_env_keys() -> tuple[str, ...]:
    return _GITHUB_ENV_KEYS


def build_github_cli_env(token: str | None) -> dict[str, str]:
    env_values = {
        GH_PROMPT_DISABLED_ENV_KEY: "1",
        GH_NO_UPDATE_NOTIFIER_ENV_KEY: "1",
        GH_NO_EXTENSION_UPDATE_NOTIFIER_ENV_KEY: "1",
    }
    normalized_token = normalize_github_token(token)
    if normalized_token is not None:
        env_values[GH_TOKEN_ENV_KEY] = normalized_token
        env_values[GITHUB_TOKEN_ENV_KEY] = normalized_token
    return env_values
