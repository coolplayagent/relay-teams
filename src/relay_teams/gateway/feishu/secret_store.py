# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from relay_teams.gateway.feishu.models import FeishuTriggerSecretConfig
from relay_teams.secrets import AppSecretStore, get_secret_store

_LEGACY_KEYRING_SERVICE_NAME = "agent-teams.feishu-trigger"
_LEGACY_FILE_NAME = "feishu_trigger_secrets.json"
_NAMESPACE = "feishu_trigger"


class FeishuTriggerSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_secret_config(
        self, config_dir: Path, trigger_id: str
    ) -> FeishuTriggerSecretConfig:
        self._migrate_legacy_storage(config_dir, trigger_id)
        secrets_by_field = self._secret_store.get_owner_secrets(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=trigger_id.strip(),
        )
        return FeishuTriggerSecretConfig(
            app_secret=_normalize_secret(secrets_by_field.get("app_secret")),
            verification_token=_normalize_secret(
                secrets_by_field.get("verification_token")
            ),
            encrypt_key=_normalize_secret(secrets_by_field.get("encrypt_key")),
        )

    def set_secret_config(
        self,
        config_dir: Path,
        trigger_id: str,
        secret_config: FeishuTriggerSecretConfig,
    ) -> None:
        owner_id = trigger_id.strip()
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=owner_id,
            field_name="app_secret",
            value=_normalize_secret(secret_config.app_secret),
        )
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=owner_id,
            field_name="verification_token",
            value=_normalize_secret(secret_config.verification_token),
        )
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=owner_id,
            field_name="encrypt_key",
            value=_normalize_secret(secret_config.encrypt_key),
        )

    def delete_secret_config(self, config_dir: Path, trigger_id: str) -> None:
        self._secret_store.delete_owner(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=trigger_id.strip(),
        )

    def can_persist_secrets(self) -> bool:
        return True

    def _migrate_legacy_storage(self, config_dir: Path, trigger_id: str) -> None:
        self._migrate_legacy_keyring(config_dir, trigger_id)
        self._migrate_legacy_file(config_dir, trigger_id)

    def _migrate_legacy_keyring(self, config_dir: Path, trigger_id: str) -> None:
        if keyring is None:
            return
        account_name = f"{config_dir.expanduser().resolve()}::{trigger_id}"
        for field_name in ("app_secret", "verification_token", "encrypt_key"):
            try:
                legacy_value = keyring.get_password(
                    _LEGACY_KEYRING_SERVICE_NAME,
                    f"{account_name}:{field_name}",
                )
            except Exception:
                continue
            normalized = _normalize_secret(legacy_value)
            if normalized is None:
                continue
            migrated = self._secret_store.migrate_legacy_secret(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=trigger_id.strip(),
                field_name=field_name,
                value=normalized,
            )
            if not migrated:
                continue
            try:
                keyring.delete_password(
                    _LEGACY_KEYRING_SERVICE_NAME,
                    f"{account_name}:{field_name}",
                )
            except Exception:
                continue

    def _migrate_legacy_file(self, config_dir: Path, trigger_id: str) -> None:
        legacy_file = config_dir.expanduser().resolve() / _LEGACY_FILE_NAME
        if not legacy_file.exists() or not legacy_file.is_file():
            return
        try:
            payload = json.loads(legacy_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        raw_entry = payload.get(trigger_id)
        if not isinstance(raw_entry, dict):
            return
        changed = False
        for field_name in ("app_secret", "verification_token", "encrypt_key"):
            raw_value = raw_entry.get(field_name)
            normalized = _normalize_secret(
                raw_value if isinstance(raw_value, str) else None
            )
            if normalized is None:
                continue
            migrated = self._secret_store.migrate_legacy_secret(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=trigger_id.strip(),
                field_name=field_name,
                value=normalized,
            )
            changed = changed or migrated
        if not changed:
            return
        payload.pop(trigger_id, None)
        legacy_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


_FEISHU_TRIGGER_SECRET_STORE = FeishuTriggerSecretStore()


def get_feishu_trigger_secret_store() -> FeishuTriggerSecretStore:
    return _FEISHU_TRIGGER_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
