# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from agent_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
)
from agent_teams.secrets import AppSecretStore, get_secret_store

_MODEL_PROFILE_SECRET_NAMESPACE = "model_profile"
_MODEL_PROFILE_SECRET_FIELD = "api_key"


class ModelConfigManager:
    def __init__(
        self,
        *,
        config_dir: Path,
        secret_store: AppSecretStore | None = None,
    ) -> None:
        self._config_dir: Path = config_dir
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_model_config(self) -> dict[str, JsonValue]:
        model_file = self._config_dir / "model.json"
        if model_file.exists():
            config = _load_json_object(model_file)
            config = self._migrate_legacy_profile_api_keys(
                config, model_file=model_file
            )
            return self._hydrate_model_config(config)
        return {}

    def get_model_profiles(self) -> dict[str, dict[str, JsonValue]]:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return {}
        config = _load_json_object(model_file)
        config = self._migrate_legacy_profile_api_keys(config, model_file=model_file)
        default_profile_name = _resolve_default_profile_name(config)
        result: dict[str, dict[str, JsonValue]] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                continue
            api_key, has_api_key = self._resolve_api_key(name, profile)
            result[name] = {
                "provider": profile.get("provider", "openai_compatible"),
                "model": profile.get("model", ""),
                "base_url": profile.get("base_url", ""),
                "api_key": api_key,
                "has_api_key": has_api_key,
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
            config = self._migrate_legacy_profile_api_keys(
                config, model_file=model_file
            )
        existing_profile = config.get(name)
        if source_name is not None and source_name != name:
            existing_profile = config.get(source_name, existing_profile)
        current_secret = (
            self._get_profile_secret(source_name)
            if source_name is not None and source_name != name
            else self._get_profile_secret(name)
        )
        config[name], next_secret, preserve_secret = (
            _prepare_profile_api_key_for_storage(
                existing_profile=existing_profile,
                next_profile=profile,
                current_secret=current_secret,
            )
        )
        if source_name is not None and source_name != name:
            config.pop(source_name, None)
        _normalize_default_profile_flags(config, preferred_name=name)
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")
        self._apply_profile_secret_update(
            name=name,
            source_name=source_name,
            next_secret=next_secret,
            preserve_secret=preserve_secret,
        )

    def delete_model_profile(self, name: str) -> None:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            self._delete_profile_secret(name)
            return
        config = _load_json_object(model_file)
        if name in config:
            del config[name]
            _normalize_default_profile_flags(config)
            _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")
        self._delete_profile_secret(name)

    def save_model_config(self, config: dict[str, JsonValue]) -> None:
        model_file = self._config_dir / "model.json"
        existing_config = (
            self._migrate_legacy_profile_api_keys(
                _load_json_object(model_file),
                model_file=model_file,
            )
            if model_file.exists()
            else {}
        )
        next_config: dict[str, JsonValue] = {}
        secret_updates: dict[str, tuple[str | None, bool]] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                next_config[name] = profile
                continue
            current_secret = self._get_profile_secret(name)
            next_profile, next_secret, preserve_secret = (
                _prepare_profile_api_key_for_storage(
                    existing_profile=existing_config.get(name),
                    next_profile=profile,
                    current_secret=current_secret,
                )
            )
            next_config[name] = next_profile
            secret_updates[name] = (next_secret, preserve_secret)
        _normalize_default_profile_flags(next_config)
        _ = model_file.write_text(dumps(next_config, indent=2), encoding="utf-8")
        existing_profile_names = {
            profile_name
            for profile_name, profile in existing_config.items()
            if isinstance(profile, dict)
        }
        next_profile_names = {
            profile_name
            for profile_name, profile in next_config.items()
            if isinstance(profile, dict)
        }
        for removed_name in sorted(existing_profile_names - next_profile_names):
            self._delete_profile_secret(removed_name)
        for profile_name, (next_secret, preserve_secret) in secret_updates.items():
            if next_secret is not None:
                self._set_profile_secret(profile_name, next_secret)
                continue
            if preserve_secret:
                continue
            self._delete_profile_secret(profile_name)

    def _hydrate_model_config(
        self,
        config: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        hydrated: dict[str, JsonValue] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                hydrated[name] = profile
                continue
            next_profile = dict(profile)
            api_key, has_api_key = self._resolve_api_key(name, profile)
            if has_api_key:
                next_profile["api_key"] = api_key
            hydrated[name] = next_profile
        return hydrated

    def _resolve_api_key(
        self,
        profile_name: str,
        profile: dict[str, JsonValue],
    ) -> tuple[str, bool]:
        raw_api_key = profile.get("api_key")
        if isinstance(raw_api_key, str) and raw_api_key.strip():
            return raw_api_key, True
        secret_value = self._get_profile_secret(profile_name)
        if secret_value is None:
            return "", False
        return secret_value, True

    def _migrate_legacy_profile_api_keys(
        self,
        config: dict[str, JsonValue],
        *,
        model_file: Path,
    ) -> dict[str, JsonValue]:
        changed = False
        for name, profile in config.items():
            if not isinstance(profile, dict):
                continue
            raw_api_key = profile.get("api_key")
            if not isinstance(raw_api_key, str):
                continue
            normalized_api_key = raw_api_key.strip()
            if not normalized_api_key or _is_env_placeholder(normalized_api_key):
                continue
            self._secret_store.migrate_legacy_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=name,
                field_name=_MODEL_PROFILE_SECRET_FIELD,
                value=normalized_api_key,
            )
            profile.pop("api_key", None)
            changed = True
        if changed:
            model_file.write_text(dumps(config, indent=2), encoding="utf-8")
        return config

    def _get_profile_secret(self, profile_name: str | None) -> str | None:
        if profile_name is None:
            return None
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_SECRET_FIELD,
        )

    def _set_profile_secret(self, profile_name: str, api_key: str) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_SECRET_FIELD,
            value=api_key,
        )

    def _delete_profile_secret(self, profile_name: str) -> None:
        self._secret_store.delete_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_SECRET_FIELD,
        )

    def _apply_profile_secret_update(
        self,
        *,
        name: str,
        source_name: str | None,
        next_secret: str | None,
        preserve_secret: bool,
    ) -> None:
        if source_name is not None and source_name != name:
            if next_secret is not None:
                self._set_profile_secret(name, next_secret)
                self._delete_profile_secret(source_name)
                return
            if preserve_secret:
                self._secret_store.rename_owner(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    from_owner_id=source_name,
                    to_owner_id=name,
                )
                return
            self._delete_profile_secret(source_name)
            self._delete_profile_secret(name)
            return
        if next_secret is not None:
            self._set_profile_secret(name, next_secret)
            return
        if preserve_secret:
            return
        self._delete_profile_secret(name)


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    try:
        raw = cast(object, loads(file_path.read_text("utf-8")))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}


def _prepare_profile_api_key_for_storage(
    *,
    existing_profile: object,
    next_profile: dict[str, JsonValue],
    current_secret: str | None,
) -> tuple[dict[str, JsonValue], str | None, bool]:
    merged_profile = dict(next_profile)
    if isinstance(existing_profile, dict) and "is_default" not in merged_profile:
        existing_is_default = existing_profile.get("is_default")
        if isinstance(existing_is_default, bool):
            merged_profile["is_default"] = existing_is_default
    next_api_key = merged_profile.get("api_key")
    if isinstance(next_api_key, str) and next_api_key.strip():
        normalized_api_key = next_api_key.strip()
        if _is_env_placeholder(normalized_api_key):
            merged_profile["api_key"] = normalized_api_key
            return merged_profile, None, False
        merged_profile.pop("api_key", None)
        return merged_profile, normalized_api_key, False

    if isinstance(existing_profile, dict):
        existing_api_key = existing_profile.get("api_key")
        if isinstance(existing_api_key, str) and existing_api_key.strip():
            merged_profile["api_key"] = existing_api_key.strip()
            return merged_profile, None, False

    merged_profile.pop("api_key", None)
    if current_secret is not None:
        return merged_profile, None, True
    return merged_profile, None, False


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


def _is_env_placeholder(value: str) -> bool:
    return value.startswith("${") and value.endswith("}")
