# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS


class ModelConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir: Path = config_dir

    def get_model_config(self) -> dict[str, JsonValue]:
        model_file = self._config_dir / "model.json"
        if model_file.exists():
            return _load_json_object(model_file)
        return {}

    def get_model_profiles(self) -> dict[str, dict[str, JsonValue]]:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return {}
        config = _load_json_object(model_file)
        default_profile_name = _resolve_default_profile_name(config)
        result: dict[str, dict[str, JsonValue]] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                continue
            result[name] = {
                "provider": profile.get("provider", "openai_compatible"),
                "model": profile.get("model", ""),
                "base_url": profile.get("base_url", ""),
                "api_key": profile.get("api_key", ""),
                "has_api_key": bool(profile.get("api_key")),
                "ssl_verify": profile.get("ssl_verify"),
                "temperature": profile.get("temperature", 0.7),
                "top_p": profile.get("top_p", 1.0),
                "max_tokens": profile.get("max_tokens", 100000),
                "context_window": profile.get("context_window"),
                "is_default": name == default_profile_name,
                "connect_timeout_seconds": profile.get(
                    "connect_timeout_seconds",
                    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
                ),
            }
        return result

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, JsonValue],
        *,
        source_name: str | None = None,
    ) -> None:
        model_file = self._config_dir / "model.json"
        config: dict[str, JsonValue] = {}
        if model_file.exists():
            config = _load_json_object(model_file)
        existing_profile = config.get(name)
        if source_name is not None and source_name != name:
            existing_profile = config.get(source_name, existing_profile)
        config[name] = _merge_profile_api_key(
            existing_profile=existing_profile,
            next_profile=profile,
        )
        if source_name is not None and source_name != name:
            config.pop(source_name, None)
        _normalize_default_profile_flags(config, preferred_name=name)
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def delete_model_profile(self, name: str) -> None:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return
        config = _load_json_object(model_file)
        if name in config:
            del config[name]
            _normalize_default_profile_flags(config)
            _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def save_model_config(self, config: dict[str, JsonValue]) -> None:
        model_file = self._config_dir / "model.json"
        _normalize_default_profile_flags(config)
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    try:
        raw = cast(object, loads(file_path.read_text("utf-8")))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}


def _merge_profile_api_key(
    *,
    existing_profile: object,
    next_profile: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    merged_profile = dict(next_profile)
    if isinstance(existing_profile, dict) and "is_default" not in merged_profile:
        existing_is_default = existing_profile.get("is_default")
        if isinstance(existing_is_default, bool):
            merged_profile["is_default"] = existing_is_default
    next_api_key = merged_profile.get("api_key")
    if isinstance(next_api_key, str) and next_api_key.strip():
        return merged_profile

    if not isinstance(existing_profile, dict):
        merged_profile.pop("api_key", None)
        return merged_profile

    existing_api_key = existing_profile.get("api_key")
    if isinstance(existing_api_key, str) and existing_api_key.strip():
        merged_profile["api_key"] = existing_api_key
        return merged_profile

    merged_profile.pop("api_key", None)
    return merged_profile


def _normalize_default_profile_flags(
    config: dict[str, JsonValue],
    *,
    preferred_name: str | None = None,
) -> None:
    profile_names = [
        name for name, profile in config.items() if isinstance(profile, dict)
    ]
    if not profile_names:
        return

    current_default = _resolve_default_profile_name(config)
    next_default = (
        preferred_name
        if _profile_requests_default(config, preferred_name)
        else current_default
    )
    if next_default not in profile_names:
        next_default = current_default
    if next_default not in profile_names:
        next_default = sorted(profile_names)[0]

    for name in profile_names:
        profile = config.get(name)
        if not isinstance(profile, dict):
            continue
        profile["is_default"] = name == next_default


def _resolve_default_profile_name(config: dict[str, JsonValue]) -> str | None:
    profile_names = [
        name for name, profile in config.items() if isinstance(profile, dict)
    ]
    if not profile_names:
        return None

    explicit_defaults: list[str] = []
    for name in profile_names:
        profile = config.get(name)
        if not isinstance(profile, dict):
            continue
        if profile.get("is_default") is True:
            explicit_defaults.append(name)
    if explicit_defaults:
        return sorted(explicit_defaults)[0]
    if "default" in profile_names:
        return "default"
    if len(profile_names) == 1:
        return profile_names[0]
    return sorted(profile_names)[0]


def _profile_requests_default(
    config: dict[str, JsonValue],
    preferred_name: str | None,
) -> bool:
    if preferred_name is None:
        return False
    profile = config.get(preferred_name)
    if not isinstance(profile, dict):
        return False
    return profile.get("is_default") is True
