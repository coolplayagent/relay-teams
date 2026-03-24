# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from agent_teams.feishu.models import FeishuTriggerSecretConfig
from agent_teams.logger import get_logger

logger = get_logger(__name__)

_KEYRING_SERVICE_NAME = "agent-teams.feishu-trigger"
_SECRETS_FILE_NAME = "feishu_trigger_secrets.json"


class FeishuTriggerSecretStore:
    def get_secret_config(self, config_dir: Path, trigger_id: str) -> FeishuTriggerSecretConfig:
        if self._has_keyring_backend():
            return self._get_from_keyring(config_dir, trigger_id)
        return self._get_from_file(config_dir, trigger_id)

    def set_secret_config(
        self,
        config_dir: Path,
        trigger_id: str,
        secret_config: FeishuTriggerSecretConfig,
    ) -> None:
        if self._has_keyring_backend():
            self._set_to_keyring(config_dir, trigger_id, secret_config)
            return
        self._set_to_file(config_dir, trigger_id, secret_config)

    def delete_secret_config(self, config_dir: Path, trigger_id: str) -> None:
        if self._has_keyring_backend():
            self._delete_from_keyring(config_dir, trigger_id)
            return
        self._delete_from_file(config_dir, trigger_id)

    def can_persist_secrets(self) -> bool:
        return True

    # -- keyring backend ---------------------------------------------------

    def _has_keyring_backend(self) -> bool:
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def _get_from_keyring(
        self,
        config_dir: Path,
        trigger_id: str,
    ) -> FeishuTriggerSecretConfig:
        assert keyring is not None
        account_name = self._account_name(config_dir, trigger_id)
        try:
            return FeishuTriggerSecretConfig(
                app_secret=_normalize_secret(
                    keyring.get_password(_KEYRING_SERVICE_NAME, f"{account_name}:app_secret")
                ),
                verification_token=_normalize_secret(
                    keyring.get_password(
                        _KEYRING_SERVICE_NAME,
                        f"{account_name}:verification_token",
                    )
                ),
                encrypt_key=_normalize_secret(
                    keyring.get_password(_KEYRING_SERVICE_NAME, f"{account_name}:encrypt_key")
                ),
            )
        except Exception:
            return FeishuTriggerSecretConfig()

    def _set_to_keyring(
        self,
        config_dir: Path,
        trigger_id: str,
        secret_config: FeishuTriggerSecretConfig,
    ) -> None:
        assert keyring is not None
        account_name = self._account_name(config_dir, trigger_id)
        try:
            self._keyring_set_or_delete(
                f"{account_name}:app_secret",
                secret_config.app_secret,
            )
            self._keyring_set_or_delete(
                f"{account_name}:verification_token",
                secret_config.verification_token,
            )
            self._keyring_set_or_delete(
                f"{account_name}:encrypt_key",
                secret_config.encrypt_key,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist Feishu trigger secrets to the system keyring."
            ) from exc

    def _delete_from_keyring(self, config_dir: Path, trigger_id: str) -> None:
        assert keyring is not None
        account_name = self._account_name(config_dir, trigger_id)
        for suffix in ("app_secret", "verification_token", "encrypt_key"):
            try:
                keyring.delete_password(
                    _KEYRING_SERVICE_NAME,
                    f"{account_name}:{suffix}",
                )
            except Exception:
                continue

    def _keyring_set_or_delete(self, account_name: str, value: str | None) -> None:
        assert keyring is not None
        normalized = _normalize_secret(value)
        if normalized is None:
            try:
                keyring.delete_password(_KEYRING_SERVICE_NAME, account_name)
            except Exception:
                return
            return
        keyring.set_password(_KEYRING_SERVICE_NAME, account_name, normalized)

    # -- file backend ------------------------------------------------------

    def _secrets_file_path(self, config_dir: Path) -> Path:
        return config_dir.expanduser().resolve() / _SECRETS_FILE_NAME

    def _load_all_file_secrets(self, config_dir: Path) -> dict[str, dict[str, str | None]]:
        path = self._secrets_file_path(config_dir)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save_all_file_secrets(
        self,
        config_dir: Path,
        all_secrets: dict[str, dict[str, str | None]],
    ) -> None:
        path = self._secrets_file_path(config_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(all_secrets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_from_file(
        self,
        config_dir: Path,
        trigger_id: str,
    ) -> FeishuTriggerSecretConfig:
        all_secrets = self._load_all_file_secrets(config_dir)
        entry = all_secrets.get(trigger_id)
        if not isinstance(entry, dict):
            return FeishuTriggerSecretConfig()
        return FeishuTriggerSecretConfig(
            app_secret=_normalize_secret(entry.get("app_secret")),
            verification_token=_normalize_secret(entry.get("verification_token")),
            encrypt_key=_normalize_secret(entry.get("encrypt_key")),
        )

    def _set_to_file(
        self,
        config_dir: Path,
        trigger_id: str,
        secret_config: FeishuTriggerSecretConfig,
    ) -> None:
        all_secrets = self._load_all_file_secrets(config_dir)
        all_secrets[trigger_id] = {
            "app_secret": secret_config.app_secret,
            "verification_token": secret_config.verification_token,
            "encrypt_key": secret_config.encrypt_key,
        }
        self._save_all_file_secrets(config_dir, all_secrets)

    def _delete_from_file(self, config_dir: Path, trigger_id: str) -> None:
        all_secrets = self._load_all_file_secrets(config_dir)
        if trigger_id not in all_secrets:
            return
        del all_secrets[trigger_id]
        self._save_all_file_secrets(config_dir, all_secrets)

    # -- shared helpers ----------------------------------------------------

    def _account_name(self, config_dir: Path, trigger_id: str) -> str:
        return f"{config_dir.expanduser().resolve()}::{trigger_id}"

    def _get_backend(self) -> object | None:
        if keyring is None:
            return None
        try:
            backend = keyring.get_keyring()
        except Exception:
            return None
        if backend is None:
            return None
        return backend


_FEISHU_TRIGGER_SECRET_STORE = FeishuTriggerSecretStore()


def get_feishu_trigger_secret_store() -> FeishuTriggerSecretStore:
    return _FEISHU_TRIGGER_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
