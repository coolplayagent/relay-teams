# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from relay_teams.providers.maas_auth import maas_password_secret_field_name
from relay_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAAS_BASE_URL,
    MaaSAuthConfig,
    ModelRequestHeader,
    ProviderType,
)
from relay_teams.providers.model_header_utils import (
    model_header_secret_field_name,
    normalize_model_request_headers_payload,
)
from relay_teams.providers.known_model_context_windows import (
    infer_known_context_window,
)
from relay_teams.secrets import AppSecretStore, get_secret_store

_MODEL_PROFILE_SECRET_NAMESPACE = "model_profile"
_MODEL_PROFILE_SECRET_FIELD = "api_key"
_MODEL_PROFILE_MAAS_PASSWORD_FIELD = maas_password_secret_field_name()


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
            normalized_profile = _normalize_profile_context_window(profile)
            headers = self._resolve_headers(name, normalized_profile)
            maas_auth = self._resolve_maas_auth(name, normalized_profile)
            result[name] = {
                "provider": normalized_profile.get("provider", "openai_compatible"),
                "model": normalized_profile.get("model", ""),
                "base_url": normalized_profile.get("base_url", ""),
                "api_key": api_key,
                "has_api_key": has_api_key,
                "headers": [binding.model_dump(mode="json") for binding in headers],
                "maas_auth": (
                    _build_maas_auth_profile_payload(maas_auth)
                    if maas_auth is not None
                    else None
                ),
                "ssl_verify": normalized_profile.get("ssl_verify"),
                "temperature": normalized_profile.get("temperature", 0.7),
                "top_p": normalized_profile.get("top_p", 1.0),
                "max_tokens": normalized_profile.get("max_tokens"),
                "context_window": normalized_profile.get("context_window"),
                "fallback_policy_id": normalized_profile.get("fallback_policy_id"),
                "fallback_priority": normalized_profile.get("fallback_priority", 0),
                "is_default": name == default_profile_name,
                "connect_timeout_seconds": normalized_profile.get(
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
            if source_name not in config:
                raise KeyError(f"Model profile not found: {source_name}")
            existing_profile = config.get(source_name, existing_profile)
        current_secret = (
            self._get_profile_secret(source_name)
            if source_name is not None and source_name != name
            else self._get_profile_secret(name)
        )
        current_maas_password = (
            self._get_profile_maas_password(source_name)
            if source_name is not None and source_name != name
            else self._get_profile_maas_password(name)
        )
        normalized_next_profile = _normalize_profile_context_window(profile)
        config[name], next_secret, preserve_secret = (
            _prepare_profile_api_key_for_storage(
                existing_profile=existing_profile,
                next_profile=normalized_next_profile,
                current_secret=current_secret,
            )
        )
        config[name], next_secret, preserve_secret = _drop_api_key_for_maas_profile(
            profile=cast(dict[str, JsonValue], config[name]),
            next_secret=next_secret,
            preserve_secret=preserve_secret,
        )
        config[name] = self._prepare_profile_headers_for_storage(
            profile_name=name,
            existing_profile=existing_profile,
            next_profile=cast(dict[str, JsonValue], config[name]),
            source_name=source_name,
        )
        (
            config[name],
            next_maas_password,
            preserve_maas_password,
        ) = self._prepare_profile_maas_auth_for_storage(
            profile_name=name,
            existing_profile=existing_profile,
            next_profile=cast(dict[str, JsonValue], config[name]),
            source_name=source_name,
            current_password=current_maas_password,
        )
        if cast(dict[str, JsonValue], config[name]).get("max_tokens") is None:
            cast(dict[str, JsonValue], config[name]).pop("max_tokens", None)
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
        self._apply_profile_maas_password_update(
            name=name,
            source_name=source_name,
            next_password=next_maas_password,
            preserve_password=preserve_maas_password,
        )
        self._sync_profile_header_secrets(
            profile_name=name,
            existing_profile=existing_profile,
            next_profile=normalized_next_profile,
            source_name=source_name,
        )

    def delete_model_profile(self, name: str) -> None:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            raise KeyError(f"Model profile not found: {name}")
        config = _load_json_object(model_file)
        if name not in config:
            raise KeyError(f"Model profile not found: {name}")
        del config[name]
        _normalize_default_profile_flags(config)
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")
        self._delete_profile_secret_owner(name)

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
        maas_password_updates: dict[str, tuple[str | None, bool]] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                next_config[name] = profile
                continue
            current_secret = self._get_profile_secret(name)
            current_maas_password = self._get_profile_maas_password(name)
            normalized_next_profile = _normalize_profile_context_window(profile)
            next_profile, next_secret, preserve_secret = (
                _prepare_profile_api_key_for_storage(
                    existing_profile=existing_config.get(name),
                    next_profile=normalized_next_profile,
                    current_secret=current_secret,
                )
            )
            next_profile, next_secret, preserve_secret = _drop_api_key_for_maas_profile(
                profile=next_profile,
                next_secret=next_secret,
                preserve_secret=preserve_secret,
            )
            next_profile = self._prepare_profile_headers_for_storage(
                profile_name=name,
                existing_profile=existing_config.get(name),
                next_profile=next_profile,
                source_name=None,
            )
            (
                next_profile,
                next_maas_password,
                preserve_maas_password,
            ) = self._prepare_profile_maas_auth_for_storage(
                profile_name=name,
                existing_profile=existing_config.get(name),
                next_profile=next_profile,
                source_name=None,
                current_password=current_maas_password,
            )
            next_config[name] = next_profile
            secret_updates[name] = (next_secret, preserve_secret)
            maas_password_updates[name] = (
                next_maas_password,
                preserve_maas_password,
            )
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
            self._delete_profile_secret_owner(removed_name)
        for profile_name, (next_secret, preserve_secret) in secret_updates.items():
            if next_secret is not None:
                self._set_profile_secret(profile_name, next_secret)
            elif not preserve_secret:
                self._delete_profile_secret(profile_name)
            next_maas_password, preserve_maas_password = maas_password_updates.get(
                profile_name,
                (None, False),
            )
            if next_maas_password is not None:
                self._set_profile_maas_password(profile_name, next_maas_password)
            elif not preserve_maas_password:
                self._delete_profile_maas_password(profile_name)
            next_profile = next_config.get(profile_name)
            if isinstance(next_profile, dict) and isinstance(
                config.get(profile_name), dict
            ):
                self._sync_profile_header_secrets(
                    profile_name=profile_name,
                    existing_profile=existing_config.get(profile_name),
                    next_profile=_normalize_profile_context_window(
                        cast(dict[str, JsonValue], config[profile_name])
                    ),
                    source_name=None,
                )

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
            next_profile = _normalize_profile_context_window(next_profile)
            api_key, has_api_key = self._resolve_api_key(name, profile)
            if has_api_key:
                next_profile["api_key"] = api_key
            headers = self._resolve_headers(name, profile)
            if headers:
                next_profile["headers"] = [
                    binding.model_dump(mode="json") for binding in headers
                ]
            maas_auth = self._resolve_maas_auth(name, profile)
            if maas_auth is not None:
                next_profile["maas_auth"] = maas_auth.model_dump(mode="json")
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

    def _resolve_headers(
        self,
        profile_name: str,
        profile: dict[str, JsonValue],
    ) -> tuple[ModelRequestHeader, ...]:
        raw_headers = profile.get("headers")
        bindings = normalize_model_request_headers_payload(raw_headers)
        resolved_bindings: list[ModelRequestHeader] = []
        for binding in bindings:
            value = binding.value
            if value is None and binding.secret:
                value = self._secret_store.get_secret(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    owner_id=profile_name,
                    field_name=model_header_secret_field_name(binding.name),
                )
            resolved_bindings.append(
                binding.model_copy(
                    update={
                        "value": value,
                        "configured": value is not None,
                    }
                )
            )
        return tuple(resolved_bindings)

    def _resolve_maas_auth(
        self,
        profile_name: str,
        profile: dict[str, JsonValue],
    ) -> MaaSAuthConfig | None:
        raw_maas_auth = profile.get("maas_auth")
        if raw_maas_auth is None:
            return None
        if not isinstance(raw_maas_auth, dict):
            raise ValueError("maas_auth must be an object")
        resolved_payload: dict[str, str] = {}
        username = raw_maas_auth.get("username")
        if isinstance(username, str) and username.strip():
            resolved_payload["username"] = username.strip()
        password = raw_maas_auth.get("password")
        if not isinstance(password, str) or not password.strip():
            password = self._secret_store.get_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=profile_name,
                field_name=_MODEL_PROFILE_MAAS_PASSWORD_FIELD,
            )
        if isinstance(password, str) and password.strip():
            resolved_payload["password"] = password.strip()
        return MaaSAuthConfig.model_validate(resolved_payload)

    def _prepare_profile_headers_for_storage(
        self,
        *,
        profile_name: str,
        existing_profile: object,
        next_profile: dict[str, JsonValue],
        source_name: str | None,
    ) -> dict[str, JsonValue]:
        merged_profile = dict(next_profile)
        if "headers" not in merged_profile:
            if isinstance(existing_profile, dict) and "headers" in existing_profile:
                merged_profile["headers"] = existing_profile["headers"]
            return merged_profile

        incoming_headers = normalize_model_request_headers_payload(
            merged_profile.get("headers")
        )
        stored_headers: list[dict[str, JsonValue]] = []
        current_owner = (
            source_name
            if source_name is not None and source_name != profile_name
            else profile_name
        )
        existing_bindings = (
            self._resolve_headers(current_owner, existing_profile)
            if isinstance(existing_profile, dict)
            else ()
        )
        existing_by_name = {
            binding.name.casefold(): binding for binding in existing_bindings
        }
        for binding in incoming_headers:
            if binding.secret:
                existing_binding = existing_by_name.get(binding.name.casefold())
                if (
                    binding.value is None
                    and binding.configured is not False
                    and (existing_binding is None or existing_binding.value is None)
                ):
                    raise ValueError(
                        f"Header '{binding.name}' requires a value the first time it is configured."
                    )
                stored_headers.append(
                    {
                        "name": binding.name,
                        "secret": True,
                        "configured": False,
                    }
                )
                continue
            if binding.value is None:
                raise ValueError(
                    f"Non-secret header '{binding.name}' requires a value."
                )
            stored_headers.append(
                {
                    "name": binding.name,
                    "value": binding.value,
                    "secret": False,
                    "configured": True,
                }
            )
        merged_profile["headers"] = cast(JsonValue, stored_headers)
        return merged_profile

    def _sync_profile_header_secrets(
        self,
        *,
        profile_name: str,
        existing_profile: object,
        next_profile: dict[str, JsonValue],
        source_name: str | None,
    ) -> None:
        current_owner = (
            source_name
            if source_name is not None and source_name != profile_name
            else profile_name
        )
        if not isinstance(existing_profile, dict):
            existing_bindings: tuple[ModelRequestHeader, ...] = ()
        else:
            existing_bindings = self._resolve_headers(current_owner, existing_profile)
            if (
                not existing_bindings
                and source_name is not None
                and source_name != profile_name
            ):
                existing_bindings = self._resolve_headers(
                    profile_name, existing_profile
                )

        if "headers" not in next_profile:
            if source_name is not None and source_name != profile_name:
                self._rename_profile_header_secrets(
                    from_owner_id=source_name,
                    to_owner_id=profile_name,
                )
            return

        next_bindings = normalize_model_request_headers_payload(
            next_profile.get("headers")
        )
        existing_by_name = {
            binding.name.casefold(): binding for binding in existing_bindings
        }
        kept_names: set[str] = set()
        for binding in next_bindings:
            normalized_name = binding.name.casefold()
            kept_names.add(normalized_name)
            field_name = model_header_secret_field_name(binding.name)
            if not binding.secret:
                self._secret_store.delete_secret(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    owner_id=profile_name,
                    field_name=field_name,
                )
                continue
            if binding.value is not None:
                self._secret_store.set_secret(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    owner_id=profile_name,
                    field_name=field_name,
                    value=binding.value,
                )
                continue
            existing_binding = existing_by_name.get(normalized_name)
            if existing_binding is not None and existing_binding.value is not None:
                self._secret_store.set_secret(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    owner_id=profile_name,
                    field_name=field_name,
                    value=existing_binding.value,
                )
                continue
            if binding.configured is False:
                self._secret_store.delete_secret(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    owner_id=profile_name,
                    field_name=field_name,
                )
                continue
            raise ValueError(
                f"Header '{binding.name}' requires a value the first time it is configured."
            )

        for binding in existing_bindings:
            if binding.name.casefold() in kept_names:
                continue
            self._secret_store.delete_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=current_owner,
                field_name=model_header_secret_field_name(binding.name),
            )
        if source_name is not None and source_name != profile_name:
            self._delete_profile_header_secrets(source_name)

    def _rename_profile_header_secrets(
        self,
        *,
        from_owner_id: str,
        to_owner_id: str,
    ) -> None:
        if from_owner_id == to_owner_id:
            return
        existing_fields = self._secret_store.list_owner_fields(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=from_owner_id,
        )
        header_fields = tuple(
            field_name
            for field_name in existing_fields
            if field_name.startswith("header:")
        )
        if not header_fields:
            return
        for field_name in header_fields:
            value = self._secret_store.get_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=from_owner_id,
                field_name=field_name,
            )
            if value is None:
                continue
            self._secret_store.set_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=to_owner_id,
                field_name=field_name,
                value=value,
            )
        for field_name in header_fields:
            self._secret_store.delete_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=from_owner_id,
                field_name=field_name,
            )

    def _delete_profile_header_secrets(self, profile_name: str) -> None:
        for field_name in self._secret_store.list_owner_fields(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
        ):
            if not field_name.startswith("header:"):
                continue
            self._secret_store.delete_secret(
                self._config_dir,
                namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                owner_id=profile_name,
                field_name=field_name,
            )

    def _prepare_profile_maas_auth_for_storage(
        self,
        *,
        profile_name: str,
        existing_profile: object,
        next_profile: dict[str, JsonValue],
        source_name: str | None,
        current_password: str | None,
    ) -> tuple[dict[str, JsonValue], str | None, bool]:
        merged_profile = dict(next_profile)
        provider_raw = merged_profile.get(
            "provider", ProviderType.OPENAI_COMPATIBLE.value
        )
        if provider_raw != ProviderType.MAAS.value:
            merged_profile.pop("maas_auth", None)
            return merged_profile, None, False
        if "maas_auth" not in merged_profile:
            if isinstance(existing_profile, dict) and "maas_auth" in existing_profile:
                merged_profile["maas_auth"] = existing_profile["maas_auth"]
                return merged_profile, None, current_password is not None
            raise ValueError("MAAS model profile requires maas_auth configuration.")
        raw_maas_auth = merged_profile.get("maas_auth")
        if not isinstance(raw_maas_auth, dict):
            raise ValueError("maas_auth must be an object")
        current_owner = (
            source_name
            if source_name is not None and source_name != profile_name
            else profile_name
        )
        existing_maas_auth = (
            self._resolve_maas_auth(current_owner, existing_profile)
            if isinstance(existing_profile, dict)
            else None
        )
        password = raw_maas_auth.get("password")
        if isinstance(password, str) and password.strip():
            next_password = password.strip()
            preserve_password = False
        elif existing_maas_auth is not None and existing_maas_auth.password is not None:
            next_password = None
            preserve_password = True
        elif current_password is not None:
            next_password = None
            preserve_password = True
        else:
            raise ValueError(
                "MAAS auth password requires a value the first time it is configured."
            )
        username = raw_maas_auth.get("username")
        validated = MaaSAuthConfig.model_validate(
            {
                "username": username,
                "password": next_password
                or current_password
                or (
                    existing_maas_auth.password
                    if existing_maas_auth is not None
                    else None
                ),
            }
        )
        merged_profile["maas_auth"] = cast(
            JsonValue,
            {
                "username": validated.username,
            },
        )
        return merged_profile, next_password, preserve_password

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

    def _get_profile_maas_password(self, profile_name: str | None) -> str | None:
        if profile_name is None:
            return None
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_MAAS_PASSWORD_FIELD,
        )

    def _set_profile_maas_password(self, profile_name: str, password: str) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_MAAS_PASSWORD_FIELD,
            value=password,
        )

    def _delete_profile_maas_password(self, profile_name: str) -> None:
        self._secret_store.delete_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_MAAS_PASSWORD_FIELD,
        )

    def _delete_profile_secret_owner(self, profile_name: str) -> None:
        self._secret_store.delete_owner(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
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

    def _apply_profile_maas_password_update(
        self,
        *,
        name: str,
        source_name: str | None,
        next_password: str | None,
        preserve_password: bool,
    ) -> None:
        if source_name is not None and source_name != name:
            if next_password is not None:
                self._set_profile_maas_password(name, next_password)
                self._delete_profile_maas_password(source_name)
                return
            if preserve_password:
                existing_password = self._get_profile_maas_password(source_name)
                if existing_password is not None:
                    self._set_profile_maas_password(name, existing_password)
                self._delete_profile_maas_password(source_name)
                return
            self._delete_profile_maas_password(source_name)
            self._delete_profile_maas_password(name)
            return
        if next_password is not None:
            self._set_profile_maas_password(name, next_password)
            return
        if preserve_password:
            return
        self._delete_profile_maas_password(name)


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


def _drop_api_key_for_maas_profile(
    *,
    profile: dict[str, JsonValue],
    next_secret: str | None,
    preserve_secret: bool,
) -> tuple[dict[str, JsonValue], str | None, bool]:
    provider_raw = profile.get("provider", ProviderType.OPENAI_COMPATIBLE.value)
    if provider_raw != ProviderType.MAAS.value:
        return profile, next_secret, preserve_secret
    sanitized = dict(profile)
    sanitized.pop("api_key", None)
    return sanitized, None, False


def _normalize_profile_provider_defaults(
    profile: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    normalized_profile = dict(profile)
    provider_raw = normalized_profile.get(
        "provider", ProviderType.OPENAI_COMPATIBLE.value
    )
    if provider_raw == ProviderType.MAAS.value:
        normalized_profile["base_url"] = DEFAULT_MAAS_BASE_URL
    return normalized_profile


def _normalize_profile_context_window(
    profile: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    normalized_profile = _normalize_profile_provider_defaults(profile)
    explicit_context_window = normalized_profile.get("context_window")
    if isinstance(explicit_context_window, int) and explicit_context_window > 0:
        return normalized_profile
    provider_raw = normalized_profile.get(
        "provider", ProviderType.OPENAI_COMPATIBLE.value
    )
    model_raw = normalized_profile.get("model")
    if not isinstance(provider_raw, str) or not isinstance(model_raw, str):
        return normalized_profile
    try:
        provider = ProviderType(provider_raw)
    except ValueError:
        return normalized_profile
    inferred_context_window = infer_known_context_window(
        provider=provider,
        model=model_raw,
    )
    if inferred_context_window is None:
        return normalized_profile
    normalized_profile["context_window"] = inferred_context_window
    return normalized_profile


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


def _build_maas_auth_profile_payload(
    maas_auth: MaaSAuthConfig,
) -> dict[str, JsonValue]:
    return {
        "username": maas_auth.username,
        "password": maas_auth.password or "",
        "has_password": maas_auth.password is not None,
    }
