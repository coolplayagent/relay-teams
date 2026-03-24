# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from agent_teams.feishu.models import FeishuTriggerSecretConfig
from agent_teams.feishu.secret_store import FeishuTriggerSecretStore


def _make_store_file_backend() -> FeishuTriggerSecretStore:
    store = FeishuTriggerSecretStore()
    store._has_keyring_backend = lambda: False  # type: ignore[assignment]
    return store


class TestFileBackendFallback:
    def test_set_and_get(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        secret = FeishuTriggerSecretConfig(
            app_secret="s1",
            verification_token="t1",
            encrypt_key="k1",
        )
        store.set_secret_config(tmp_path, "trigger-a", secret)
        got = store.get_secret_config(tmp_path, "trigger-a")
        assert got.app_secret == "s1"
        assert got.verification_token == "t1"
        assert got.encrypt_key == "k1"

    def test_multiple_triggers_isolated(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        store.set_secret_config(
            tmp_path,
            "t1",
            FeishuTriggerSecretConfig(app_secret="secret-a"),
        )
        store.set_secret_config(
            tmp_path,
            "t2",
            FeishuTriggerSecretConfig(app_secret="secret-b"),
        )
        assert store.get_secret_config(tmp_path, "t1").app_secret == "secret-a"
        assert store.get_secret_config(tmp_path, "t2").app_secret == "secret-b"

    def test_get_missing_trigger(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        got = store.get_secret_config(tmp_path, "no-such-trigger")
        assert got.app_secret is None
        assert got.verification_token is None
        assert got.encrypt_key is None

    def test_delete_trigger(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        store.set_secret_config(
            tmp_path,
            "t1",
            FeishuTriggerSecretConfig(app_secret="s"),
        )
        store.delete_secret_config(tmp_path, "t1")
        got = store.get_secret_config(tmp_path, "t1")
        assert got.app_secret is None

    def test_delete_nonexistent_trigger_noop(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        store.delete_secret_config(tmp_path, "ghost")

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        store.set_secret_config(
            tmp_path,
            "t1",
            FeishuTriggerSecretConfig(app_secret="old"),
        )
        store.set_secret_config(
            tmp_path,
            "t1",
            FeishuTriggerSecretConfig(app_secret="new", verification_token="vt"),
        )
        got = store.get_secret_config(tmp_path, "t1")
        assert got.app_secret == "new"
        assert got.verification_token == "vt"

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        secrets_file = tmp_path / "feishu_trigger_secrets.json"
        secrets_file.write_text("not json", encoding="utf-8")
        got = store.get_secret_config(tmp_path, "t1")
        assert got.app_secret is None

    def test_can_persist_secrets_always_true(self) -> None:
        store = _make_store_file_backend()
        assert store.can_persist_secrets() is True

    def test_file_written_as_json(self, tmp_path: Path) -> None:
        store = _make_store_file_backend()
        store.set_secret_config(
            tmp_path,
            "t1",
            FeishuTriggerSecretConfig(app_secret="s"),
        )
        secrets_file = tmp_path / "feishu_trigger_secrets.json"
        data = json.loads(secrets_file.read_text(encoding="utf-8"))
        assert data["t1"]["app_secret"] == "s"
