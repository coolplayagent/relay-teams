# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import os

CLAWHUB_TOKEN_ENV_KEY = "CLAWHUB_TOKEN"
CLAWHUB_SITE_ENV_KEY = "CLAWHUB_SITE"
CLAWHUB_REGISTRY_ENV_KEY = "CLAWHUB_REGISTRY"
_LEGACY_CLAWHUB_SITE_ENV_KEY = "CLAWDHUB_SITE"
_LEGACY_CLAWHUB_REGISTRY_ENV_KEY = "CLAWDHUB_REGISTRY"
DEFAULT_CLAWHUB_CN_SITE = "https://mirror-cn.clawhub.com"
DEFAULT_CLAWHUB_CN_REGISTRY = DEFAULT_CLAWHUB_CN_SITE
_MANAGED_CLAWHUB_ENV_KEYS = (CLAWHUB_TOKEN_ENV_KEY,)
_CHINA_LOCALE_ENV_KEYS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
_CHINA_TIMEZONE_ENV_KEYS = ("TZ", "TIMEZONE")
_CHINA_TIMEZONE_MARKERS = (
    "asia/shanghai",
    "asia/chongqing",
    "asia/harbin",
    "asia/urumqi",
    "prc",
)


def normalize_clawhub_token(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value


def normalize_clawhub_site(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value


def normalize_clawhub_registry(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value


def resolve_clawhub_token_from_env(env_values: Mapping[str, str]) -> str | None:
    for key in _MANAGED_CLAWHUB_ENV_KEYS:
        resolved = normalize_clawhub_token(env_values.get(key))
        if resolved is not None:
            return resolved
    return None


def resolve_clawhub_site_from_env(env_values: Mapping[str, str]) -> str | None:
    for key in (CLAWHUB_SITE_ENV_KEY, _LEGACY_CLAWHUB_SITE_ENV_KEY):
        resolved = normalize_clawhub_site(env_values.get(key))
        if resolved is not None:
            return resolved
    return None


def resolve_clawhub_registry_from_env(env_values: Mapping[str, str]) -> str | None:
    for key in (CLAWHUB_REGISTRY_ENV_KEY, _LEGACY_CLAWHUB_REGISTRY_ENV_KEY):
        resolved = normalize_clawhub_registry(env_values.get(key))
        if resolved is not None:
            return resolved
    return None


def resolve_default_clawhub_site(
    env_values: Mapping[str, str] | None = None,
) -> str | None:
    resolved_env = os.environ if env_values is None else env_values
    explicit_site = resolve_clawhub_site_from_env(resolved_env)
    if explicit_site is not None:
        return explicit_site
    if _is_china_environment(resolved_env):
        return DEFAULT_CLAWHUB_CN_SITE
    return None


def resolve_default_clawhub_registry(
    env_values: Mapping[str, str] | None = None,
) -> str | None:
    resolved_env = os.environ if env_values is None else env_values
    explicit_registry = resolve_clawhub_registry_from_env(resolved_env)
    if explicit_registry is not None:
        return explicit_registry
    if _is_china_environment(resolved_env):
        return DEFAULT_CLAWHUB_CN_REGISTRY
    return None


def clawhub_env_keys() -> tuple[str, ...]:
    return _MANAGED_CLAWHUB_ENV_KEYS


def build_clawhub_cli_env(
    token: str | None,
    *,
    site: str | None = None,
    registry: str | None = None,
    env_values: Mapping[str, str] | None = None,
) -> dict[str, str]:
    output: dict[str, str] = {}
    normalized_token = normalize_clawhub_token(token)
    if normalized_token is not None:
        output[CLAWHUB_TOKEN_ENV_KEY] = normalized_token
    normalized_site = normalize_clawhub_site(site) or resolve_default_clawhub_site(
        env_values=env_values
    )
    if normalized_site is not None:
        output[CLAWHUB_SITE_ENV_KEY] = normalized_site
        output[_LEGACY_CLAWHUB_SITE_ENV_KEY] = normalized_site
    normalized_registry = normalize_clawhub_registry(
        registry
    ) or resolve_default_clawhub_registry(env_values=env_values)
    if normalized_registry is not None:
        output[CLAWHUB_REGISTRY_ENV_KEY] = normalized_registry
        output[_LEGACY_CLAWHUB_REGISTRY_ENV_KEY] = normalized_registry
    return output


def _is_china_environment(env_values: Mapping[str, str]) -> bool:
    for key in _CHINA_LOCALE_ENV_KEYS:
        raw_value = env_values.get(key)
        if raw_value is None:
            continue
        lowered_value = raw_value.strip().lower()
        if "zh_cn" in lowered_value or "zh-cn" in lowered_value:
            return True
    for key in _CHINA_TIMEZONE_ENV_KEYS:
        raw_value = env_values.get(key)
        if raw_value is None:
            continue
        lowered_value = raw_value.strip().lower()
        if any(marker in lowered_value for marker in _CHINA_TIMEZONE_MARKERS):
            return True
    for key in ("COUNTRY", "COUNTRY_CODE", "REGION"):
        raw_value = env_values.get(key)
        if raw_value is None:
            continue
        if raw_value.strip().lower() in {"cn", "china"}:
            return True
    return False
