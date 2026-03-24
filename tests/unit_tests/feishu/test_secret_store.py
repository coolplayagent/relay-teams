# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_teams.feishu.models import FeishuTriggerSecretConfig
from agent_teams.feishu.secret_store import FeishuTriggerSecretStore


def _make_store_without_keyring() -> FeishuTriggerSecretStore:
    store = FeishuTriggerSecretStore()
    store.can_persist_secrets = lambda: False  # type: ignore[assignment]
    return store


class TestEnvVarFallback:
    def test_get_secret_config_falls_back_to_env_vars(self) -> None:
        store = _make_store_without_keyring()
        env = {
            "FEISHU_APP_SECRET": "secret-from-env",
            "FEISHU_VERIFICATION_TOKEN": "token-from-env",
            "FEISHU_ENCRYPT_KEY": "key-from-env",
        }
        with patch(
            "agent_teams.feishu.secret_store.load_merged_env_vars",
            return_value=env,
        ):
            config = store.get_secret_config(Path("/tmp/cfg"), "trigger-1")

        assert config.app_secret == "secret-from-env"
        assert config.verification_token == "token-from-env"
        assert config.encrypt_key == "key-from-env"

    def test_get_secret_config_env_partial(self) -> None:
        store = _make_store_without_keyring()
        env = {"FEISHU_APP_SECRET": "only-secret"}
        with patch(
            "agent_teams.feishu.secret_store.load_merged_env_vars",
            return_value=env,
        ):
            config = store.get_secret_config(Path("/tmp/cfg"), "trigger-2")

        assert config.app_secret == "only-secret"
        assert config.verification_token is None
        assert config.encrypt_key is None

    def test_get_secret_config_env_empty(self) -> None:
        store = _make_store_without_keyring()
        with patch(
            "agent_teams.feishu.secret_store.load_merged_env_vars",
            return_value={},
        ):
            config = store.get_secret_config(Path("/tmp/cfg"), "trigger-3")

        assert config.app_secret is None
        assert config.verification_token is None
        assert config.encrypt_key is None

    def test_get_secret_config_env_strips_whitespace(self) -> None:
        store = _make_store_without_keyring()
        env = {"FEISHU_APP_SECRET": "  padded  "}
        with patch(
            "agent_teams.feishu.secret_store.load_merged_env_vars",
            return_value=env,
        ):
            config = store.get_secret_config(Path("/tmp/cfg"), "trigger-4")

        assert config.app_secret == "padded"

    def test_get_secret_config_env_blank_is_none(self) -> None:
        store = _make_store_without_keyring()
        env = {"FEISHU_APP_SECRET": "   "}
        with patch(
            "agent_teams.feishu.secret_store.load_merged_env_vars",
            return_value=env,
        ):
            config = store.get_secret_config(Path("/tmp/cfg"), "trigger-5")

        assert config.app_secret is None

    def test_set_secret_config_raises_with_env_guidance(self) -> None:
        store = _make_store_without_keyring()
        with pytest.raises(RuntimeError, match="FEISHU_APP_SECRET"):
            store.set_secret_config(
                Path("/tmp/cfg"),
                "trigger-1",
                FeishuTriggerSecretConfig(app_secret="s"),
            )

    def test_delete_secret_config_noop_without_keyring(self) -> None:
        store = _make_store_without_keyring()
        store.delete_secret_config(Path("/tmp/cfg"), "trigger-1")
