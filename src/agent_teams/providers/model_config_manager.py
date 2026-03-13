# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
from agent_teams.shared_types.json_types import JsonObject


class ModelConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir: Path = config_dir

    def get_model_config(self) -> JsonObject:
        model_file = self._config_dir / "model.json"
        if model_file.exists():
            return _load_json_object(model_file)
        return {}

    def get_model_profiles(self) -> dict[str, JsonObject]:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return {}
        config = _load_json_object(model_file)
        result: dict[str, JsonObject] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                continue
            result[name] = {
                "provider": profile.get("provider", "openai_compatible"),
                "model": profile.get("model", ""),
                "base_url": profile.get("base_url", ""),
                "has_api_key": bool(profile.get("api_key")),
                "ssl_verify": profile.get("ssl_verify"),
                "temperature": profile.get("temperature", 0.7),
                "top_p": profile.get("top_p", 1.0),
                "max_tokens": profile.get("max_tokens", 4096),
                "connect_timeout_seconds": profile.get(
                    "connect_timeout_seconds",
                    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
                ),
            }
        return result

    def save_model_profile(
        self,
        name: str,
        profile: JsonObject,
        *,
        source_name: str | None = None,
    ) -> None:
        model_file = self._config_dir / "model.json"
        config: JsonObject = {}
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
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def delete_model_profile(self, name: str) -> None:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return
        config = _load_json_object(model_file)
        if name in config:
            del config[name]
            _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def save_model_config(self, config: JsonObject) -> None:
        model_file = self._config_dir / "model.json"
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")


def _load_json_object(file_path: Path) -> JsonObject:
    try:
        raw = cast(object, loads(file_path.read_text("utf-8")))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return cast(JsonObject, raw)
    return {}


def _merge_profile_api_key(
    *,
    existing_profile: object,
    next_profile: JsonObject,
) -> JsonObject:
    merged_profile = dict(next_profile)
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
