# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from agent_teams.env.runtime_env import load_merged_env_vars
from agent_teams.feishu.models import FeishuTriggerSecretConfig

_KEYRING_SERVICE_NAME = "agent-teams.feishu-trigger"

_ENV_APP_SECRET = "FEISHU_APP_SECRET"
_ENV_VERIFICATION_TOKEN = "FEISHU_VERIFICATION_TOKEN"
_ENV_ENCRYPT_KEY = "FEISHU_ENCRYPT_KEY"


class FeishuTriggerSecretStore:
    def get_secret_config(self, config_dir: Path, trigger_id: str) -> FeishuTriggerSecretConfig:
        if not self.can_persist_secrets():
            return _load_secret_config_from_env()
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

    def set_secret_config(
        self,
        config_dir: Path,
        trigger_id: str,
        secret_config: FeishuTriggerSecretConfig,
    ) -> None:
        if not self.can_persist_secrets():
            raise RuntimeError(
                "Feishu trigger secret persistence requires a usable system keyring backend. "
                "On systems without keyring, set FEISHU_APP_SECRET, FEISHU_VERIFICATION_TOKEN, "
                "and FEISHU_ENCRYPT_KEY environment variables or in the .env file instead."
            )
        assert keyring is not None
        account_name = self._account_name(config_dir, trigger_id)
        try:
            self._set_or_delete(
                f"{account_name}:app_secret",
                secret_config.app_secret,
            )
            self._set_or_delete(
                f"{account_name}:verification_token",
                secret_config.verification_token,
            )
            self._set_or_delete(
                f"{account_name}:encrypt_key",
                secret_config.encrypt_key,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist Feishu trigger secrets to the system keyring."
            ) from exc

    def delete_secret_config(self, config_dir: Path, trigger_id: str) -> None:
        if not self.can_persist_secrets():
            return
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

    def can_persist_secrets(self) -> bool:
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def _set_or_delete(self, account_name: str, value: str | None) -> None:
        assert keyring is not None
        normalized = _normalize_secret(value)
        if normalized is None:
            try:
                keyring.delete_password(_KEYRING_SERVICE_NAME, account_name)
            except Exception:
                return
            return
        keyring.set_password(_KEYRING_SERVICE_NAME, account_name, normalized)

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


def _load_secret_config_from_env() -> FeishuTriggerSecretConfig:
    env = load_merged_env_vars()
    return FeishuTriggerSecretConfig(
        app_secret=_normalize_secret(env.get(_ENV_APP_SECRET)),
        verification_token=_normalize_secret(env.get(_ENV_VERIFICATION_TOKEN)),
        encrypt_key=_normalize_secret(env.get(_ENV_ENCRYPT_KEY)),
    )


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
