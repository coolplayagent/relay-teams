# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
from typing import cast

from relay_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAAS_BASE_URL,
)
from relay_teams.providers.maas_auth import maas_password_secret_field_name
from relay_teams.providers.model_config_manager import ModelConfigManager
from relay_teams.secrets import AppSecretStore


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
    assert profiles["default"]["fallback_policy_id"] is None
    assert profiles["default"]["fallback_priority"] == 0
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


def test_save_model_profile_and_get_model_profiles_with_secret_headers(
    tmp_path: Path,
) -> None:
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
            "headers": [
                {
                    "name": "Authorization",
                    "value": "Bearer header-secret",
                    "secret": True,
                }
            ],
        },
    )

    profiles = manager.get_model_profiles()

    assert profiles["default"]["api_key"] == ""
    assert profiles["default"]["has_api_key"] is False
    headers = cast(list[dict[str, JsonValue]], profiles["default"]["headers"])
    assert headers[0]["name"] == "Authorization"
    assert headers[0]["value"] == "Bearer header-secret"
    model_payload = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    assert model_payload["default"]["headers"] == [
        {
            "name": "Authorization",
            "secret": True,
            "configured": False,
        }
    ]


def test_save_model_profile_preserves_existing_secret_header_when_blank(
    tmp_path: Path,
) -> None:
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
            "headers": [
                {
                    "name": "Authorization",
                    "value": "Bearer first-secret",
                    "secret": True,
                }
            ],
        },
    )

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "headers": [
                {
                    "name": "Authorization",
                    "secret": True,
                    "configured": True,
                }
            ],
        },
    )

    config = manager.get_model_config()
    saved_profile = cast(dict[str, JsonValue], config["default"])
    saved_headers = cast(list[dict[str, JsonValue]], saved_profile["headers"])
    assert saved_profile["model"] == "kimi-k2.5"
    assert saved_headers[0]["value"] == "Bearer first-secret"


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
    assert profiles["default"]["max_tokens"] is None


def test_get_model_profiles_returns_fallback_settings(tmp_path: Path) -> None:
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
                    "fallback_policy_id": "same_provider_then_other_provider",
                    "fallback_priority": 7,
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = manager.get_model_profiles()

    assert profiles["default"]["fallback_policy_id"] == (
        "same_provider_then_other_provider"
    )
    assert profiles["default"]["fallback_priority"] == 7


def test_get_model_profiles_preserves_raw_image_capability_override_state(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key": "secret-key",
            "capabilities": {
                "input": {
                    "text": True,
                    "image": None,
                },
                "output": {
                    "text": True,
                },
            },
        },
    )

    profiles = manager.get_model_profiles()
    raw_capabilities = cast(dict[str, JsonValue], profiles["default"]["capabilities"])
    resolved_capabilities = cast(
        dict[str, JsonValue],
        profiles["default"]["resolved_capabilities"],
    )
    raw_input = cast(dict[str, JsonValue], raw_capabilities["input"])
    resolved_input = cast(dict[str, JsonValue], resolved_capabilities["input"])

    assert raw_input["image"] is None
    assert resolved_input["image"] is True
    assert profiles["default"]["input_modalities"] == ["image"]


def test_save_model_profile_preserves_existing_fallback_settings_when_omitted(
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
                    "fallback_policy_id": "same_provider_then_other_provider",
                    "fallback_priority": 7,
                }
            }
        ),
        encoding="utf-8",
    )

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "gpt-4.1",
            "base_url": "https://example.test/v1",
            "temperature": 0.2,
            "top_p": 1.0,
        },
    )

    profiles = manager.get_model_profiles()
    saved_payload = json.loads(model_file.read_text(encoding="utf-8"))

    assert profiles["default"]["fallback_policy_id"] == (
        "same_provider_then_other_provider"
    )
    assert profiles["default"]["fallback_priority"] == 7
    assert saved_payload["default"]["fallback_policy_id"] == (
        "same_provider_then_other_provider"
    )
    assert saved_payload["default"]["fallback_priority"] == 7


def test_get_model_profiles_infers_known_context_window_when_missing(
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

    assert profiles["default"]["context_window"] == 128000


def test_save_model_profile_omits_max_tokens_when_unset(tmp_path: Path) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "gpt-4.1",
            "base_url": "https://example.test/v1",
            "api_key": "secret-key",
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": None,
        },
    )

    config = manager.get_model_config()
    saved_profile = cast(dict[str, JsonValue], config["default"])

    assert "max_tokens" not in saved_profile


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


def test_save_model_profile_persists_inferred_context_window_when_missing(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(config_dir=tmp_path)

    manager.save_model_profile(
        "default",
        {
            "provider": "openai_compatible",
            "model": "gpt-4.1",
            "base_url": "https://example.test/v1",
            "api_key": "secret-key",
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 1024,
        },
    )

    config = manager.get_model_config()
    saved_profile = cast(dict[str, JsonValue], config["default"])

    assert saved_profile["context_window"] == 1000000


def test_save_model_profile_renames_and_preserves_existing_api_key(
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


def test_save_model_profile_stores_maas_password_in_secret_store(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(
        config_dir=tmp_path,
        secret_store=_FileOnlySecretStore(),
    )

    manager.save_model_profile(
        "maas-profile",
        {
            "provider": "maas",
            "model": "maas-chat",
            "base_url": "https://maas.example/api/v2",
            "maas_auth": {
                "username": "relay-user",
                "password": "relay-password",
            },
        },
    )

    profiles = manager.get_model_profiles()
    model_payload = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    secrets_payload = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    maas_auth = cast(dict[str, JsonValue], profiles["maas-profile"]["maas_auth"])

    assert cast(str, profiles["maas-profile"]["base_url"]) == DEFAULT_MAAS_BASE_URL
    assert maas_auth["username"] == "relay-user"
    assert maas_auth["password"] == "relay-password"
    assert maas_auth["has_password"] is True
    assert model_payload["maas-profile"]["base_url"] == DEFAULT_MAAS_BASE_URL
    assert model_payload["maas-profile"]["maas_auth"] == {
        "username": "relay-user",
    }
    assert {
        "namespace": "model_profile",
        "owner_id": "maas-profile",
        "field_name": maas_password_secret_field_name(),
        "storage": "file",
        "value": "relay-password",
    } in secrets_payload["entries"]


def test_save_model_profile_preserves_existing_maas_password_when_blank(
    tmp_path: Path,
) -> None:
    manager = ModelConfigManager(
        config_dir=tmp_path,
        secret_store=_FileOnlySecretStore(),
    )
    manager.save_model_profile(
        "maas-profile",
        {
            "provider": "maas",
            "model": "maas-chat",
            "base_url": "https://maas.example/api/v2",
            "maas_auth": {
                "username": "relay-user",
                "password": "relay-password",
            },
        },
    )

    manager.save_model_profile(
        "maas-profile",
        {
            "provider": "maas",
            "model": "maas-chat-v2",
            "base_url": "https://maas.example/api/v2",
            "maas_auth": {
                "username": "relay-user-2",
            },
        },
    )

    config = manager.get_model_config()
    saved_profile = cast(dict[str, JsonValue], config["maas-profile"])
    saved_maas_auth = cast(dict[str, JsonValue], saved_profile["maas_auth"])

    assert saved_profile["model"] == "maas-chat-v2"
    assert saved_profile["base_url"] == DEFAULT_MAAS_BASE_URL
    assert saved_maas_auth["password"] == "relay-password"
    assert saved_maas_auth["username"] == "relay-user-2"


def test_switching_profile_to_maas_removes_stale_api_key_secret(tmp_path: Path) -> None:
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
        },
    )

    manager.save_model_profile(
        "default",
        {
            "provider": "maas",
            "model": "maas-chat",
            "base_url": "https://maas.example/api/v2",
            "maas_auth": {
                "username": "relay-user",
                "password": "relay-password",
            },
        },
    )

    secrets_payload = json.loads(
        (tmp_path / "secrets.json").read_text(encoding="utf-8")
    )
    field_names = {entry["field_name"] for entry in secrets_payload["entries"]}

    assert "api_key" not in field_names
    assert maas_password_secret_field_name() in field_names
