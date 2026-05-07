# -*- coding: utf-8 -*-
from __future__ import annotations

from json import JSONDecodeError, dumps, loads
from pathlib import Path

from pydantic import JsonValue

from relay_teams.plugins.plugin_models import PluginScope
from relay_teams.secrets import AppSecretStore, get_secret_store

_NAMESPACE = "plugin_user_config"


class PluginUserConfigSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_field(
        self,
        config_dir: Path,
        *,
        plugin_name: str,
        scope: PluginScope,
        field_name: str,
    ) -> JsonValue | None:
        stored_value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_owner_id(plugin_name=plugin_name, scope=scope),
            field_name=field_name,
        )
        if stored_value is None:
            return None
        return _decode_secret_value(stored_value)

    def has_field(
        self,
        config_dir: Path,
        *,
        plugin_name: str,
        scope: PluginScope,
        field_name: str,
    ) -> bool:
        return field_name in self._secret_store.list_owner_fields(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_owner_id(plugin_name=plugin_name, scope=scope),
        )

    def set_field(
        self,
        config_dir: Path,
        *,
        plugin_name: str,
        scope: PluginScope,
        field_name: str,
        value: JsonValue | None,
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_owner_id(plugin_name=plugin_name, scope=scope),
            field_name=field_name,
            value=_encode_secret_value(value),
        )

    def delete_field(
        self,
        config_dir: Path,
        *,
        plugin_name: str,
        scope: PluginScope,
        field_name: str,
    ) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_owner_id(plugin_name=plugin_name, scope=scope),
            field_name=field_name,
        )

    def delete_plugin(
        self,
        config_dir: Path,
        *,
        plugin_name: str,
        scope: PluginScope,
    ) -> None:
        self._secret_store.delete_owner(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_owner_id(plugin_name=plugin_name, scope=scope),
        )


_PLUGIN_USER_CONFIG_SECRET_STORE = PluginUserConfigSecretStore()


def get_plugin_user_config_secret_store() -> PluginUserConfigSecretStore:
    return _PLUGIN_USER_CONFIG_SECRET_STORE


def _owner_id(*, plugin_name: str, scope: PluginScope) -> str:
    return f"{scope.value}:{plugin_name}"


def _encode_secret_value(value: JsonValue | None) -> str | None:
    return dumps({"value": value}, ensure_ascii=False, separators=(",", ":"))


def _decode_secret_value(value: str) -> JsonValue:
    try:
        payload = loads(value)
    except JSONDecodeError:
        return value
    if not isinstance(payload, dict):
        return value
    return payload.get("value")
