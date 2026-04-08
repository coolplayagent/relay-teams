from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


def assert_integration_model_config_uses_fake_llm(*, config_dir: Path) -> None:
    model_file = config_dir / "model.json"
    payload = json.loads(model_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Integration model.json must be a JSON object.")

    for profile_name, profile_payload in payload.items():
        if not isinstance(profile_payload, dict):
            raise ValueError(
                f"Integration model profile '{profile_name}' must be a JSON object."
            )
        base_url = profile_payload.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError(
                f"Integration model profile '{profile_name}' is missing base_url."
            )
        _assert_fake_llm_base_url(
            base_url=base_url,
            source=f"Integration model profile '{profile_name}'",
        )


def write_test_runtime_config(*, config_dir: Path, fake_llm_v1_base_url: str) -> None:
    _assert_fake_llm_base_url(
        base_url=fake_llm_v1_base_url,
        source="fake_llm_v1_base_url",
    )
    config_dir.mkdir(parents=True, exist_ok=True)

    model_config = {
        "default": {
            "model": "fake-chat-model",
            "base_url": fake_llm_v1_base_url,
            "api_key": "test-api-key",
            "context_window": 22000,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 256,
        }
    }
    (config_dir / "model.json").write_text(
        json.dumps(model_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _assert_fake_llm_base_url(*, base_url: str, source: str) -> None:
    parsed = urlparse(base_url)
    if parsed.hostname != "127.0.0.1":
        raise ValueError(f"{source} must use fake LLM on 127.0.0.1, got: {base_url}")
