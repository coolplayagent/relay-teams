# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from relay_teams.providers.model_header_utils import model_header_secret_field_name
from relay_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
from relay_teams.secrets import get_secret_store
from relay_teams.sessions.runs import runtime_config


def test_load_runtime_config_uses_project_config_dir_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "default": {
                    "model": "fake-model",
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "test-key",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_config, "get_app_config_dir", lambda: config_dir)
    monkeypatch.setattr(runtime_config, "load_merged_env_vars", lambda **kwargs: {})

    resolved = runtime_config.load_runtime_config()

    assert resolved.paths.config_dir == config_dir.resolve()
    assert resolved.paths.env_file == (config_dir / ".env").resolve()
    assert resolved.paths.roles_dir == (config_dir / "roles")
    assert resolved.paths.db_path == (config_dir / "relay_teams.db")
    assert resolved.paths.prompts_file == (config_dir / "prompts.json").resolve()
    assert resolved.llm_retry.max_retries == 5
    assert resolved.llm_retry.initial_delay_ms == 2000
    assert resolved.llm_retry.jitter is False


def test_load_runtime_config_ignores_roles_dir_env_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "default": {
                    "model": "fake-model",
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "test-key",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runtime_config,
        "load_merged_env_vars",
        lambda **kwargs: {"AGENT_TEAMS_ROLES_DIR": "roles"},
    )

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.paths.roles_dir == (config_dir / "roles")


def test_load_runtime_config_reports_missing_model_config_without_raising(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_config, "load_merged_env_vars", lambda **kwargs: {})

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.llm_profiles == {}
    assert resolved.model_status.loaded is False
    assert resolved.model_status.error is not None
    assert resolved.llm_retry.max_retries == 5


def test_load_runtime_config_reads_prompt_instructions(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "default": {
                    "model": "fake-model",
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "test-key",
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "prompts.json").write_text(
        json.dumps({"instructions": ["docs/*.md"]}),
        encoding="utf-8",
    )

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.prompt_instructions.instructions == ("docs/*.md",)


def test_load_runtime_config_rejects_invalid_prompts_config(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "prompts.json").write_text(
        json.dumps({"instructions": [1]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        runtime_config.load_runtime_config(config_dir=config_dir)

    assert "Invalid prompts.json" in str(exc_info.value)


def test_load_llm_configs_error_mentions_model_file_only(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc_info:
        runtime_config.load_llm_configs(tmp_path, {})

    assert "Please create model.json with at least one profile." in str(exc_info.value)


def test_load_llm_configs_reads_provider_field(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "plain-text-key",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].provider.value == "openai_compatible"


def test_load_llm_configs_reads_bigmodel_provider_field(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "bigmodel",
                    "model": "glm-4.5",
                    "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
                    "api_key": "plain-text-key",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].provider.value == "bigmodel"


def test_load_runtime_config_reads_explicit_default_profile_name(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "backup": {
                    "model": "backup-model",
                    "base_url": "https://backup.example/v1",
                    "api_key": "backup-key",
                    "is_default": True,
                },
                "primary": {
                    "model": "primary-model",
                    "base_url": "https://primary.example/v1",
                    "api_key": "primary-key",
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.default_model_profile == "backup"


def test_load_runtime_config_uses_first_profile_when_no_default_is_marked(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "zeta": {
                    "model": "zeta-model",
                    "base_url": "https://zeta.example/v1",
                    "api_key": "zeta-key",
                },
                "alpha": {
                    "model": "alpha-model",
                    "base_url": "https://alpha.example/v1",
                    "api_key": "alpha-key",
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.default_model_profile == "alpha"


def test_load_runtime_config_rejects_multiple_explicit_default_profiles(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "alpha": {
                    "model": "alpha-model",
                    "base_url": "https://alpha.example/v1",
                    "api_key": "alpha-key",
                    "is_default": True,
                },
                "beta": {
                    "model": "beta-model",
                    "base_url": "https://beta.example/v1",
                    "api_key": "beta-key",
                    "is_default": True,
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.model_status.loaded is False
    assert resolved.model_status.error is not None
    assert "more than one default profile" in resolved.model_status.error


def test_load_llm_configs_uses_default_connect_timeout_when_not_configured(
    tmp_path: Path,
) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "plain-text-key",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert (
        profiles["default"].connect_timeout_seconds
        == DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
    )


def test_load_llm_configs_reads_connect_timeout_seconds(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "plain-text-key",
                    "connect_timeout_seconds": 45.0,
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].connect_timeout_seconds == 45.0


def test_load_llm_configs_reads_context_window(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "plain-text-key",
                    "context_window": 128000,
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].context_window == 128000


def test_load_llm_configs_infers_known_context_window_when_missing(
    tmp_path: Path,
) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4.1",
                    "base_url": "https://example.test/v1",
                    "api_key": "plain-text-key",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].context_window == 1000000


def test_load_llm_configs_resolves_api_key_env_placeholder(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "${OPENAI_API_KEY}",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(
        tmp_path,
        {"OPENAI_API_KEY": "resolved-secret"},
    )

    assert profiles["default"].api_key == "resolved-secret"


def test_load_llm_configs_errors_when_api_key_env_placeholder_is_missing(
    tmp_path: Path,
) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "${OPENAI_API_KEY}",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        runtime_config.load_llm_configs(tmp_path, {})

    assert (
        "environment variable 'OPENAI_API_KEY' referenced by api_key is not set"
        in str(exc_info.value)
    )


def test_load_llm_configs_allows_header_only_profiles(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "headers": [
                        {
                            "name": "Authorization",
                            "value": "Bearer header-only",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].api_key is None
    assert profiles["default"].headers[0].name == "Authorization"
    assert profiles["default"].headers[0].value == "Bearer header-only"


def test_load_llm_configs_resolves_secret_headers_from_secret_store(
    tmp_path: Path,
) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "headers": [
                        {
                            "name": "Authorization",
                            "secret": True,
                            "configured": False,
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    get_secret_store().set_secret(
        tmp_path,
        namespace="model_profile",
        owner_id="default",
        field_name=model_header_secret_field_name("Authorization"),
        value="Bearer stored-secret",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].headers[0].value == "Bearer stored-secret"
