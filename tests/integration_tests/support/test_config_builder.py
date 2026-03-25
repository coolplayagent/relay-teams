# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from integration_tests.support.config_builder import (
    assert_integration_model_config_uses_fake_llm,
    write_test_runtime_config,
)


def test_write_test_runtime_config_rejects_non_local_fake_llm_url(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError, match="fake_llm_v1_base_url must use fake LLM on 127.0.0.1"
    ):
        write_test_runtime_config(
            config_dir=tmp_path / ".agent-teams",
            fake_llm_v1_base_url="https://api.example.com/v1",
        )


def test_validate_model_config_accepts_fake_llm_profiles(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    write_test_runtime_config(
        config_dir=config_dir,
        fake_llm_v1_base_url="http://127.0.0.1:18911/v1",
    )

    payload = json.loads((config_dir / "model.json").read_text(encoding="utf-8"))
    payload["secondary"] = {
        "model": "fake-chat-model-2",
        "base_url": "http://127.0.0.1:20001/v1",
        "api_key": "test-api-key",
    }
    (config_dir / "model.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert_integration_model_config_uses_fake_llm(config_dir=config_dir)


def test_validate_model_config_rejects_external_base_url(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "default": {
                    "model": "fake-chat-model",
                    "base_url": "https://api.minimaxi.com/v1",
                    "api_key": "test-api-key",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="Integration model profile 'default' must use fake LLM on 127.0.0.1",
    ):
        assert_integration_model_config_uses_fake_llm(config_dir=config_dir)
