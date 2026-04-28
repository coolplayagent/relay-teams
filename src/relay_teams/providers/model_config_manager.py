# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from relay_teams.providers.codeagent_auth import (
    codeagent_access_token_secret_field_name,
    codeagent_password_secret_field_name,
    codeagent_refresh_token_secret_field_name,
    consume_codeagent_oauth_tokens,
    get_codeagent_oauth_tokens,
)
from relay_teams.providers.maas_auth import maas_password_secret_field_name
from relay_teams.providers.model_capabilities import resolve_model_capabilities
from relay_teams.providers.model_config import (
    CodeAgentAuthMethod,
    CodeAgentAuthConfig,
    DEFAULT_CODEAGENT_BASE_URL,
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
_MODEL_PROFILE_CODEAGENT_ACCESS_TOKEN_FIELD = codeagent_access_token_secret_field_name()
_MODEL_PROFILE_CODEAGENT_PASSWORD_FIELD = codeagent_password_secret_field_name()
_MODEL_PROFILE_CODEAGENT_REFRESH_TOKEN_FIELD = (
    codeagent_refresh_token_secret_field_name()
)


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
            codeagent_auth = self._resolve_codeagent_auth(name, normalized_profile)
            raw_provider = str(normalized_profile.get("provider", "openai_compatible"))
            try:
                provider = ProviderType(raw_provider)
            except ValueError:
                provider = ProviderType.OPENAI_COMPATIBLE
            capabilities = resolve_model_capabilities(
                provider=provider,
                base_url=str(normalized_profile.get("base_url", "")),
                model_name=str(normalized_profile.get("model", "")),
                metadata=normalized_profile,
            )
            raw_capabilities = normalized_profile.get("capabilities")
            result[name] = {
                "provider": raw_provider,
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
                "codeagent_auth": (
                    _build_codeagent_auth_profile_payload(codeagent_auth)
                    if codeagent_auth is not None
                    else None
                ),
                "ssl_verify": normalized_profile.get("ssl_verify"),
                "temperature": normalized_profile.get("temperature", 0.7),
                "top_p": normalized_profile.get("top_p", 1.0),
                "max_tokens": normalized_profile.get("max_tokens"),
                "context_window": normalized_profile.get("context_window"),
                "fallback_policy_id": normalized_profile.get("fallback_policy_id"),
                "fallback_priority": normalized_profile.get("fallback_priority", 0),
                "catalog_provider_id": normalized_profile.get("catalog_provider_id"),
                "catalog_provider_name": normalized_profile.get(
                    "catalog_provider_name"
                ),
                "catalog_model_name": normalized_profile.get("catalog_model_name"),
                "is_default": name == default_profile_name,
                "connect_timeout_seconds": normalized_profile.get(
                    "connect_timeout_seconds",
                    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
                ),
                "capabilities": (
                    raw_capabilities if isinstance(raw_capabilities, dict) else None
                ),
                "resolved_capabilities": capabilities.model_dump(mode="json"),
                "input_modalities": [
                    modality.value
                    for modality in capabilities.supported_input_modalities()
                ],
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
        codeagent_storage_relevant = _profile_uses_codeagent(
            profile
        ) or _profile_uses_codeagent(existing_profile)
        current_codeagent_access_token = (
            (
                self._get_profile_codeagent_access_token(source_name)
                if source_name is not None and source_name != name
                else self._get_profile_codeagent_access_token(name)
            )
            if codeagent_storage_relevant
            else None
        )
        current_codeagent_password = (
            (
                self._get_profile_codeagent_password(source_name)
                if source_name is not None and source_name != name
                else self._get_profile_codeagent_password(name)
            )
            if codeagent_storage_relevant
            else None
        )
        current_codeagent_refresh_token = (
            (
                self._get_profile_codeagent_refresh_token(source_name)
                if source_name is not None and source_name != name
                else self._get_profile_codeagent_refresh_token(name)
            )
            if codeagent_storage_relevant
            else None
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
        config[name], next_secret, preserve_secret = (
            _drop_api_key_for_codeagent_profile(
                profile=cast(dict[str, JsonValue], config[name]),
                next_secret=next_secret,
                preserve_secret=preserve_secret,
            )
        )
        raw_header_secret_sync_profile = dict(cast(dict[str, JsonValue], config[name]))
        config[name] = self._prepare_profile_headers_for_storage(
            profile_name=name,
            existing_profile=existing_profile,
            next_profile=cast(dict[str, JsonValue], config[name]),
            source_name=source_name,
        )
        header_secret_sync_profile = _build_header_secret_sync_profile(
            raw_profile=raw_header_secret_sync_profile,
            sanitized_profile=cast(dict[str, JsonValue], config[name]),
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
        (
            config[name],
            next_codeagent_access_token,
            next_codeagent_refresh_token,
            next_codeagent_password,
            preserve_codeagent_tokens,
            preserve_codeagent_password,
            _pending_oauth_session_id,
        ) = self._prepare_profile_codeagent_auth_for_storage(
            profile_name=name,
            existing_profile=existing_profile,
            next_profile=cast(dict[str, JsonValue], config[name]),
            source_name=source_name,
            current_access_token=current_codeagent_access_token,
            current_password=current_codeagent_password,
            current_refresh_token=current_codeagent_refresh_token,
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
        self._apply_profile_codeagent_token_update(
            name=name,
            source_name=source_name,
            next_access_token=next_codeagent_access_token,
            next_refresh_token=next_codeagent_refresh_token,
            preserve_tokens=preserve_codeagent_tokens,
        )
        self._apply_profile_codeagent_password_update(
            name=name,
            source_name=source_name,
            next_password=next_codeagent_password,
            preserve_password=preserve_codeagent_password,
        )
        self._sync_profile_header_secrets(
            profile_name=name,
            existing_profile=existing_profile,
            next_profile=header_secret_sync_profile,
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
        codeagent_password_updates: dict[str, tuple[str | None, bool]] = {}
        codeagent_token_updates: dict[str, tuple[str | None, str | None, bool]] = {}
        header_secret_sync_profiles: dict[str, dict[str, JsonValue]] = {}
        pending_codeagent_oauth_sessions: dict[str, str] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                next_config[name] = profile
                continue
            current_secret = self._get_profile_secret(name)
            current_maas_password = self._get_profile_maas_password(name)
            current_codeagent_access_token = self._get_profile_codeagent_access_token(
                name
            )
            current_codeagent_password = self._get_profile_codeagent_password(name)
            current_codeagent_refresh_token = self._get_profile_codeagent_refresh_token(
                name
            )
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
            next_profile, next_secret, preserve_secret = (
                _drop_api_key_for_codeagent_profile(
                    profile=next_profile,
                    next_secret=next_secret,
                    preserve_secret=preserve_secret,
                )
            )
            raw_header_secret_sync_profile = dict(next_profile)
            next_profile = self._prepare_profile_headers_for_storage(
                profile_name=name,
                existing_profile=existing_config.get(name),
                next_profile=next_profile,
                source_name=None,
            )
            header_secret_sync_profile = _build_header_secret_sync_profile(
                raw_profile=raw_header_secret_sync_profile,
                sanitized_profile=next_profile,
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
            (
                next_profile,
                next_codeagent_access_token,
                next_codeagent_refresh_token,
                next_codeagent_password,
                preserve_codeagent_tokens,
                preserve_codeagent_password,
                pending_oauth_session_id,
            ) = self._prepare_profile_codeagent_auth_for_storage(
                profile_name=name,
                existing_profile=existing_config.get(name),
                next_profile=next_profile,
                source_name=None,
                current_access_token=current_codeagent_access_token,
                current_password=current_codeagent_password,
                current_refresh_token=current_codeagent_refresh_token,
                consume_oauth_session=False,
            )
            next_config[name] = next_profile
            secret_updates[name] = (next_secret, preserve_secret)
            maas_password_updates[name] = (
                next_maas_password,
                preserve_maas_password,
            )
            codeagent_token_updates[name] = (
                next_codeagent_access_token,
                next_codeagent_refresh_token,
                preserve_codeagent_tokens,
            )
            codeagent_password_updates[name] = (
                next_codeagent_password,
                preserve_codeagent_password,
            )
            header_secret_sync_profiles[name] = header_secret_sync_profile
            if pending_oauth_session_id is not None:
                pending_codeagent_oauth_sessions[name] = pending_oauth_session_id
        _normalize_default_profile_flags(next_config)
        for profile_name, oauth_session_id in pending_codeagent_oauth_sessions.items():
            token_result = consume_codeagent_oauth_tokens(oauth_session_id)
            if token_result is None:
                raise ValueError(
                    f"CodeAgent OAuth session became unavailable before saving profile '{profile_name}'."
                )
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
            (
                next_codeagent_access_token,
                next_codeagent_refresh_token,
                preserve_codeagent_tokens,
            ) = codeagent_token_updates.get(profile_name, (None, None, False))
            next_codeagent_password, preserve_codeagent_password = (
                codeagent_password_updates.get(profile_name, (None, False))
            )
            self._apply_profile_codeagent_token_update(
                name=profile_name,
                source_name=None,
                next_access_token=next_codeagent_access_token,
                next_refresh_token=next_codeagent_refresh_token,
                preserve_tokens=preserve_codeagent_tokens,
            )
            self._apply_profile_codeagent_password_update(
                name=profile_name,
                source_name=None,
                next_password=next_codeagent_password,
                preserve_password=preserve_codeagent_password,
            )
            next_profile = next_config.get(profile_name)
            if isinstance(next_profile, dict) and isinstance(
                config.get(profile_name), dict
            ):
                next_profile_for_header_secrets = header_secret_sync_profiles.get(
                    profile_name,
                    cast(dict[str, JsonValue], next_profile),
                )
                self._sync_profile_header_secrets(
                    profile_name=profile_name,
                    existing_profile=existing_config.get(profile_name),
                    next_profile=next_profile_for_header_secrets,
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
            codeagent_auth = self._resolve_codeagent_auth(name, profile)
            if codeagent_auth is not None:
                next_profile["codeagent_auth"] = codeagent_auth.model_dump(mode="json")
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

    def _resolve_codeagent_auth(
        self,
        profile_name: str,
        profile: dict[str, JsonValue],
    ) -> CodeAgentAuthConfig | None:
        raw_codeagent_auth = profile.get("codeagent_auth")
        if raw_codeagent_auth is None:
            return None
        if not isinstance(raw_codeagent_auth, dict):
            raise ValueError("codeagent_auth must be an object")
        resolved_payload: dict[str, str | bool] = {}
        auth_method_raw = raw_codeagent_auth.get("auth_method")
        if isinstance(auth_method_raw, str) and auth_method_raw.strip():
            resolved_payload["auth_method"] = auth_method_raw.strip()
        resolved_auth_method = (
            CodeAgentAuthMethod.PASSWORD
            if resolved_payload.get("auth_method") == CodeAgentAuthMethod.PASSWORD.value
            else CodeAgentAuthMethod.SSO
        )
        username = raw_codeagent_auth.get("username")
        if isinstance(username, str) and username.strip():
            resolved_payload["username"] = username.strip()
        if resolved_auth_method == CodeAgentAuthMethod.PASSWORD:
            password = raw_codeagent_auth.get("password")
            if isinstance(password, str) and password.strip():
                resolved_payload["password"] = password.strip()
            else:
                secret_value = self._secret_store.get_secret(
                    self._config_dir,
                    namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
                    owner_id=profile_name,
                    field_name=_MODEL_PROFILE_CODEAGENT_PASSWORD_FIELD,
                )
                if secret_value is not None:
                    resolved_payload["password"] = secret_value
            if raw_codeagent_auth.get("has_password"):
                resolved_payload["has_password"] = True
            return CodeAgentAuthConfig.model_validate(
                resolved_payload
            ).with_secret_owner(
                config_dir=self._config_dir,
                owner_id=profile_name,
            )
        oauth_session_id = raw_codeagent_auth.get("oauth_session_id")
        if isinstance(oauth_session_id, str) and oauth_session_id.strip():
            resolved_payload["oauth_session_id"] = oauth_session_id.strip()
        access_token = self._resolve_codeagent_auth_token(
            profile_name=profile_name,
            raw_auth=raw_codeagent_auth,
            field_name="access_token",
            secret_field_name=_MODEL_PROFILE_CODEAGENT_ACCESS_TOKEN_FIELD,
        )
        refresh_token = self._resolve_codeagent_auth_token(
            profile_name=profile_name,
            raw_auth=raw_codeagent_auth,
            field_name="refresh_token",
            secret_field_name=_MODEL_PROFILE_CODEAGENT_REFRESH_TOKEN_FIELD,
        )
        if access_token is not None:
            resolved_payload["access_token"] = access_token
        if refresh_token is not None:
            resolved_payload["refresh_token"] = refresh_token
        if raw_codeagent_auth.get("has_access_token") is True:
            resolved_payload["has_access_token"] = True
        if raw_codeagent_auth.get("has_refresh_token") is True:
            resolved_payload["has_refresh_token"] = True
        return CodeAgentAuthConfig.model_validate(resolved_payload).with_secret_owner(
            config_dir=self._config_dir,
            owner_id=profile_name,
        )

    def _resolve_codeagent_auth_token(
        self,
        *,
        profile_name: str,
        raw_auth: dict[str, JsonValue],
        field_name: str,
        secret_field_name: str,
    ) -> str | None:
        raw_value = raw_auth.get(field_name)
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=secret_field_name,
        )

    def _prepare_profile_headers_for_storage(
        self,
        *,
        profile_name: str,
        existing_profile: object,
        next_profile: dict[str, JsonValue],
        source_name: str | None,
    ) -> dict[str, JsonValue]:
        merged_profile = dict(next_profile)
        provider_raw = merged_profile.get(
            "provider",
            ProviderType.OPENAI_COMPATIBLE.value,
        )
        if provider_raw == ProviderType.CODEAGENT.value:
            merged_profile["headers"] = cast(JsonValue, [])
            return merged_profile
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

    def _prepare_profile_codeagent_auth_for_storage(
        self,
        *,
        profile_name: str,
        existing_profile: object,
        next_profile: dict[str, JsonValue],
        source_name: str | None,
        current_access_token: str | None,
        current_password: str | None,
        current_refresh_token: str | None,
        consume_oauth_session: bool = True,
    ) -> tuple[
        dict[str, JsonValue],
        str | None,
        str | None,
        str | None,
        bool,
        bool,
        str | None,
    ]:
        merged_profile = dict(next_profile)
        current_owner = (
            source_name
            if source_name is not None and source_name != profile_name
            else profile_name
        )
        provider_raw = merged_profile.get(
            "provider", ProviderType.OPENAI_COMPATIBLE.value
        )
        if provider_raw != ProviderType.CODEAGENT.value:
            merged_profile.pop("codeagent_auth", None)
            return (
                merged_profile,
                None,
                None,
                None,
                False,
                False,
                None,
            )
        if "codeagent_auth" not in merged_profile:
            if (
                isinstance(existing_profile, dict)
                and "codeagent_auth" in existing_profile
            ):
                merged_profile["codeagent_auth"] = existing_profile["codeagent_auth"]
                preserved_auth = (
                    self._resolve_codeagent_auth(current_owner, existing_profile)
                    if isinstance(existing_profile, dict)
                    else None
                )
                return (
                    merged_profile,
                    None,
                    None,
                    None,
                    (
                        preserved_auth is not None
                        and preserved_auth.auth_method == CodeAgentAuthMethod.SSO
                        and current_refresh_token is not None
                    ),
                    (
                        preserved_auth is not None
                        and preserved_auth.auth_method == CodeAgentAuthMethod.PASSWORD
                        and current_password is not None
                    ),
                    None,
                )
            raise ValueError(
                "CodeAgent model profile requires codeagent_auth configuration."
            )
        raw_codeagent_auth = merged_profile.get("codeagent_auth")
        if not isinstance(raw_codeagent_auth, dict):
            raise ValueError("codeagent_auth must be an object")
        existing_codeagent_auth = (
            self._resolve_codeagent_auth(current_owner, existing_profile)
            if isinstance(existing_profile, dict)
            else None
        )
        auth_method_raw = raw_codeagent_auth.get("auth_method")
        normalized_auth_method = (
            auth_method_raw.strip()
            if isinstance(auth_method_raw, str) and auth_method_raw.strip()
            else None
        )
        if normalized_auth_method not in (
            None,
            CodeAgentAuthMethod.SSO.value,
            CodeAgentAuthMethod.PASSWORD.value,
        ):
            raise ValueError("CodeAgent auth auth_method must be 'sso' or 'password'.")
        resolved_auth_method = (
            CodeAgentAuthMethod.PASSWORD
            if normalized_auth_method == CodeAgentAuthMethod.PASSWORD.value
            else CodeAgentAuthMethod.SSO
        )
        if resolved_auth_method == CodeAgentAuthMethod.PASSWORD:
            password = raw_codeagent_auth.get("password")
            next_password = (
                password.strip()
                if isinstance(password, str) and password.strip()
                else None
            )
            next_username = raw_codeagent_auth.get("username")
            normalized_username = (
                next_username.strip()
                if isinstance(next_username, str) and next_username.strip()
                else None
            )
            current_username = (
                existing_codeagent_auth.username
                if existing_codeagent_auth is not None
                else None
            )
            existing_password = (
                existing_codeagent_auth.password
                if existing_codeagent_auth is not None
                else None
            )
            stored_password = self._get_profile_codeagent_password(current_owner)
            if normalized_username is None:
                raise ValueError(
                    "CodeAgent auth username requires a value for password auth."
                )
            preserve_password = False
            if next_password is None:
                if (
                    current_username is not None
                    and normalized_username != current_username.strip()
                ):
                    raise ValueError(
                        "CodeAgent auth password must be re-entered after changing the username."
                    )
                if existing_password is not None:
                    if stored_password is not None:
                        preserve_password = True
                    else:
                        next_password = existing_password
                elif current_password is not None:
                    preserve_password = True
                else:
                    raise ValueError(
                        "CodeAgent auth password requires a value the first time it is configured."
                    )
            validated = CodeAgentAuthConfig.model_validate(
                {
                    "auth_method": CodeAgentAuthMethod.PASSWORD.value,
                    "username": normalized_username,
                    "password": next_password or current_password or existing_password,
                }
            )
            merged_profile["codeagent_auth"] = {
                "auth_method": validated.auth_method.value,
                "username": validated.username,
                "has_password": True,
            }
            return (
                merged_profile,
                None,
                None,
                next_password,
                False,
                preserve_password,
                None,
            )
        token_result = None
        pending_oauth_session_id = None
        oauth_session_id = raw_codeagent_auth.get("oauth_session_id")
        if isinstance(oauth_session_id, str) and oauth_session_id.strip():
            pending_oauth_session_id = oauth_session_id.strip()
            token_result = (
                consume_codeagent_oauth_tokens(pending_oauth_session_id)
                if consume_oauth_session
                else get_codeagent_oauth_tokens(pending_oauth_session_id)
            )
            if token_result is None:
                raise ValueError(
                    "CodeAgent OAuth session is missing, expired, or already consumed."
                )

        access_token = raw_codeagent_auth.get("access_token")
        refresh_token = raw_codeagent_auth.get("refresh_token")
        next_access_token = (
            token_result.access_token
            if token_result is not None
            else access_token.strip()
            if isinstance(access_token, str) and access_token.strip()
            else None
        )
        next_refresh_token = (
            token_result.refresh_token
            if token_result is not None
            else refresh_token.strip()
            if isinstance(refresh_token, str) and refresh_token.strip()
            else None
        )
        preserve_tokens = False
        if next_refresh_token is None:
            if (
                existing_codeagent_auth is not None
                and existing_codeagent_auth.refresh_token is not None
            ):
                preserve_tokens = True
            elif current_refresh_token is not None:
                preserve_tokens = True
            else:
                raise ValueError(
                    "CodeAgent auth requires completing SSO login before saving."
                )
        codeagent_auth_payload: dict[str, object] = {
            "access_token": next_access_token
            or current_access_token
            or (
                existing_codeagent_auth.access_token
                if existing_codeagent_auth is not None
                else None
            ),
            "refresh_token": next_refresh_token
            or current_refresh_token
            or (
                existing_codeagent_auth.refresh_token
                if existing_codeagent_auth is not None
                else None
            ),
        }
        CodeAgentAuthConfig.model_validate(
            codeagent_auth_payload,
        )
        merged_profile["codeagent_auth"] = cast(
            JsonValue,
            {
                "auth_method": CodeAgentAuthMethod.SSO.value,
                "has_access_token": bool(
                    next_access_token
                    or current_access_token
                    or (
                        existing_codeagent_auth.access_token
                        if existing_codeagent_auth is not None
                        else None
                    )
                ),
                "has_refresh_token": True,
            },
        )
        return (
            merged_profile,
            next_access_token,
            next_refresh_token,
            None,
            preserve_tokens,
            False,
            pending_oauth_session_id if not consume_oauth_session else None,
        )

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

    def _get_profile_codeagent_access_token(
        self,
        profile_name: str | None,
    ) -> str | None:
        if profile_name is None:
            return None
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_ACCESS_TOKEN_FIELD,
        )

    def _get_profile_codeagent_password(self, profile_name: str | None) -> str | None:
        if profile_name is None:
            return None
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_PASSWORD_FIELD,
        )

    def _get_profile_codeagent_refresh_token(
        self,
        profile_name: str | None,
    ) -> str | None:
        if profile_name is None:
            return None
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_REFRESH_TOKEN_FIELD,
        )

    def _set_profile_codeagent_access_token(
        self,
        profile_name: str,
        access_token: str,
    ) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_ACCESS_TOKEN_FIELD,
            value=access_token,
        )

    def _set_profile_codeagent_password(
        self,
        profile_name: str,
        password: str,
    ) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_PASSWORD_FIELD,
            value=password,
        )

    def _set_profile_codeagent_refresh_token(
        self,
        profile_name: str,
        refresh_token: str,
    ) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_REFRESH_TOKEN_FIELD,
            value=refresh_token,
        )

    def _delete_profile_codeagent_tokens(self, profile_name: str) -> None:
        self._secret_store.delete_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_ACCESS_TOKEN_FIELD,
        )
        self._secret_store.delete_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_REFRESH_TOKEN_FIELD,
        )

    def _delete_profile_codeagent_password(self, profile_name: str) -> None:
        self._secret_store.delete_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=_MODEL_PROFILE_CODEAGENT_PASSWORD_FIELD,
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

    def _apply_profile_codeagent_password_update(
        self,
        *,
        name: str,
        source_name: str | None,
        next_password: str | None,
        preserve_password: bool,
    ) -> None:
        if source_name is not None and source_name != name:
            if next_password is not None:
                self._set_profile_codeagent_password(name, next_password)
                self._delete_profile_codeagent_password(source_name)
                return
            if preserve_password:
                existing_password = self._get_profile_codeagent_password(source_name)
                if existing_password is not None:
                    self._set_profile_codeagent_password(name, existing_password)
                self._delete_profile_codeagent_password(source_name)
                return
            self._delete_profile_codeagent_password(source_name)
            self._delete_profile_codeagent_password(name)
            return
        if next_password is not None:
            self._set_profile_codeagent_password(name, next_password)
            return
        if preserve_password:
            return
        self._delete_profile_codeagent_password(name)

    def _apply_profile_codeagent_token_update(
        self,
        *,
        name: str,
        source_name: str | None,
        next_access_token: str | None,
        next_refresh_token: str | None,
        preserve_tokens: bool,
    ) -> None:
        if source_name is not None and source_name != name:
            if next_access_token is not None:
                self._set_profile_codeagent_access_token(name, next_access_token)
            if next_refresh_token is not None:
                self._set_profile_codeagent_refresh_token(name, next_refresh_token)
            if next_access_token is not None or next_refresh_token is not None:
                self._delete_profile_codeagent_tokens(source_name)
                return
            if preserve_tokens:
                source_access_token = self._get_profile_codeagent_access_token(
                    source_name
                )
                source_refresh_token = self._get_profile_codeagent_refresh_token(
                    source_name
                )
                if source_access_token is not None:
                    self._set_profile_codeagent_access_token(name, source_access_token)
                if source_refresh_token is not None:
                    self._set_profile_codeagent_refresh_token(
                        name, source_refresh_token
                    )
                self._delete_profile_codeagent_tokens(source_name)
                return
            self._delete_profile_codeagent_tokens(source_name)
            self._delete_profile_codeagent_tokens(name)
            return
        if next_access_token is not None:
            self._set_profile_codeagent_access_token(name, next_access_token)
        if next_refresh_token is not None:
            self._set_profile_codeagent_refresh_token(name, next_refresh_token)
        if next_access_token is not None or next_refresh_token is not None:
            return
        if preserve_tokens:
            return
        self._delete_profile_codeagent_tokens(name)


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
    if isinstance(existing_profile, dict):
        if "is_default" not in merged_profile:
            existing_is_default = existing_profile.get("is_default")
            if isinstance(existing_is_default, bool):
                merged_profile["is_default"] = existing_is_default
        if "fallback_policy_id" not in merged_profile:
            existing_fallback_policy_id = existing_profile.get("fallback_policy_id")
            if isinstance(existing_fallback_policy_id, str):
                merged_profile["fallback_policy_id"] = existing_fallback_policy_id
        if "fallback_priority" not in merged_profile:
            existing_fallback_priority = existing_profile.get("fallback_priority")
            if isinstance(existing_fallback_priority, int) and not isinstance(
                existing_fallback_priority, bool
            ):
                merged_profile["fallback_priority"] = existing_fallback_priority
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


def _drop_api_key_for_codeagent_profile(
    *,
    profile: dict[str, JsonValue],
    next_secret: str | None,
    preserve_secret: bool,
) -> tuple[dict[str, JsonValue], str | None, bool]:
    provider_raw = profile.get("provider", ProviderType.OPENAI_COMPATIBLE.value)
    if provider_raw != ProviderType.CODEAGENT.value:
        return profile, next_secret, preserve_secret
    sanitized = dict(profile)
    sanitized.pop("api_key", None)
    sanitized.pop("headers", None)
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
    elif provider_raw == ProviderType.CODEAGENT.value:
        normalized_profile["base_url"] = DEFAULT_CODEAGENT_BASE_URL
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


def _profile_uses_codeagent(profile: object) -> bool:
    if not isinstance(profile, dict):
        return False
    provider = profile.get("provider")
    return provider == ProviderType.CODEAGENT.value or "codeagent_auth" in profile


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


def _build_codeagent_auth_profile_payload(
    codeagent_auth: CodeAgentAuthConfig,
) -> dict[str, JsonValue]:
    if codeagent_auth.auth_method == CodeAgentAuthMethod.PASSWORD:
        return {
            "auth_method": codeagent_auth.auth_method.value,
            "username": codeagent_auth.username or "",
            "password": codeagent_auth.password or "",
            "has_password": codeagent_auth.password is not None
            or codeagent_auth.has_password,
            "has_access_token": False,
            "has_refresh_token": False,
        }
    return {
        "auth_method": codeagent_auth.auth_method.value,
        "has_access_token": codeagent_auth.access_token is not None
        or codeagent_auth.has_access_token,
        "has_refresh_token": codeagent_auth.refresh_token is not None
        or codeagent_auth.has_refresh_token,
    }


def _build_header_secret_sync_profile(
    *,
    raw_profile: dict[str, JsonValue],
    sanitized_profile: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    provider_raw = sanitized_profile.get(
        "provider",
        ProviderType.OPENAI_COMPATIBLE.value,
    )
    if provider_raw == ProviderType.CODEAGENT.value:
        return {
            "provider": cast(JsonValue, provider_raw),
            "headers": cast(JsonValue, []),
        }
    if "headers" not in raw_profile:
        return raw_profile
    sync_profile = dict(raw_profile)
    sync_profile["provider"] = cast(JsonValue, provider_raw)
    return sync_profile
