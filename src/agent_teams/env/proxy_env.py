# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import os

_PROXY_ENV_KEY_GROUPS: tuple[tuple[str, str], ...] = (
    ("HTTP_PROXY", "http_proxy"),
    ("HTTPS_PROXY", "https_proxy"),
    ("ALL_PROXY", "all_proxy"),
    ("NO_PROXY", "no_proxy"),
)


def extract_proxy_env_vars(env_values: Mapping[str, str]) -> dict[str, str]:
    proxy_env: dict[str, str] = {}
    for uppercase_key, lowercase_key in _PROXY_ENV_KEY_GROUPS:
        value = _resolve_env_value(env_values, uppercase_key, lowercase_key)
        if value is None:
            continue
        proxy_env[uppercase_key] = value
        proxy_env[lowercase_key] = value
    return proxy_env


def apply_proxy_env_to_process_env(env_values: Mapping[str, str]) -> dict[str, str]:
    proxy_env = extract_proxy_env_vars(env_values)
    for key, value in proxy_env.items():
        os.environ[key] = value
    return proxy_env


def _resolve_env_value(
    env_values: Mapping[str, str],
    uppercase_key: str,
    lowercase_key: str,
) -> str | None:
    uppercase_value = env_values.get(uppercase_key)
    if uppercase_value:
        return uppercase_value

    lowercase_value = env_values.get(lowercase_key)
    if lowercase_value:
        return lowercase_value

    return None
