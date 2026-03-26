# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
from typing import cast

from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
from agent_teams.providers.model_config_manager import ModelConfigManager
from agent_teams.secrets import AppSecretStore


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def test_get_model_config_returns_empty_when_file_missing(tmp_path: Path) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)

    assert manager.get_model_config() == {}


def test_save_model_profile_and_get_model_profiles(tmp_path: Path) -> None:
    manager = ModelConfigManager(
        config_dir=tmp_path,
        secret_store=_FileOnlySecretStore(),
    )

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://example.test/v1",
            "api_key": "secret-key",
            "temperature": 0.25,
            "top_p": 0.9,
            "max_tokens": 2000,
            "context_window": 128000,
            "connect_timeout_seconds": 45.0,
        },
    )

    profiles = manager.get_model_profiles()

    assert profiles["default"]["provider"] == "openai_compatible"
    assert profiles["default"]["api_key"] == "secret-key"
    assert profiles["default"]["has_api_key"] is True
    assert profiles["default"]["is_default"] is True
    assert profiles["default"]["temperature"] == 0.25
    assert profiles["default"]["max_tokens"] == 2000
    assert profiles["default"]["context_window"] == 128000
    assert profiles["default"]["connect_timeout_seconds"] == 45.0
    model_payload = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    assert "api_key" not in model_payload["default"]
    secrets_payload = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    assert secrets_payload["entries"] == [
        {
            "namespace": "model_profile",
            "owner_id": "default",
            "field_name": "api_key",
            "storage": "file",
            "value": "secret-key",
        }
    ]


def test_get_model_profiles_uses_default_connect_timeout_when_missing(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-key",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = manager.get_model_profiles()

    assert (
        profiles["default"]["connect_timeout_seconds"]
        == DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
    )


def test_delete_model_profile_removes_entry(tmp_path: Path) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-key",
                },
                "secondary": {
                    "provider": "echo",
                    "model": "echo",
                    "base_url": "http://localhost",
                    "api_key": "none",
                },
            }
        ),
        encoding="utf-8",
    )

    manager.delete_model_profile("default")
    config = manager.get_model_config()

    assert "default" not in config
    assert "secondary" in config
    saved_secondary = cast(dict[str, JsonValue], config["secondary"])
    assert saved_secondary["is_default"] is True


def test_save_model_profile_preserves_existing_api_key_when_blank(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-key",
                    "temperature": 0.2,
                    "top_p": 1.0,
                    "max_tokens": 1024,
                }
            }
        ),
        encoding="utf-8",
    )

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    config = manager.get_model_config()
    saved_profile = cast(dict[str, JsonValue], config["default"])

    assert saved_profile["model"] == "kimi-k2.5"
    assert saved_profile["top_p"] == 0.95
    assert saved_profile["api_key"] == "secret-key"


def test_save_model_profile_renames_and_preserves_existing_api_key(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-key",
                    "temperature": 0.2,
                    "top_p": 1.0,
                    "max_tokens": 1024,
                }
            }
        ),
        encoding="utf-8",
    )

    manager.save_model_profile(
        "renamed-profile",
        {
            "provider": "openai_compatible",
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
        source_name="default",
    )

    config = manager.get_model_config()
    saved_profile = cast(dict[str, JsonValue], config["renamed-profile"])

    assert "default" not in config
    assert saved_profile["model"] == "kimi-k2.5"
    assert saved_profile["api_key"] == "secret-key"
    assert saved_profile["is_default"] is True


def test_save_model_profile_can_switch_default_profile(tmp_path: Path) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-key",
                    "is_default": True,
                },
                "kimi": {
                    "provider": "openai_compatible",
                    "model": "kimi-k2.5",
                    "base_url": "https://api.moonshot.cn/v1",
                    "api_key": "kimi-key",
                },
            }
        ),
        encoding="utf-8",
    )

    manager.save_model_profile(
        "kimi",
        {
            "provider": "openai_compatible",
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "kimi-key",
            "is_default": True,
        },
    )

    config = manager.get_model_config()

    assert cast(dict[str, JsonValue], config["default"])["is_default"] is False
    assert cast(dict[str, JsonValue], config["kimi"])["is_default"] is True


def test_get_model_profiles_migrates_legacy_api_key_out_of_model_json(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(
        config_dir=tmp_path,
        secret_store=_FileOnlySecretStore(),
    )
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "legacy-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = manager.get_model_profiles()

    assert profiles["default"]["api_key"] == "legacy-secret"
    stored_model_payload = json.loads(model_file.read_text(encoding="utf-8"))
    assert "api_key" not in stored_model_payload["default"]
    secrets_payload = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    assert secrets_payload["entries"] == [
        {
            "namespace": "model_profile",
            "owner_id": "default",
            "field_name": "api_key",
            "storage": "file",
            "value": "legacy-secret",
        }
    ]
