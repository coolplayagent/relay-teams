from __future__ import annotations

from pathlib import Path
import json


def write_test_runtime_config(*, config_dir: Path, fake_llm_v1_base_url: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    roles_dir = config_dir / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)

    model_config = {
        "default": {
            "model": "fake-chat-model",
            "base_url": fake_llm_v1_base_url,
            "api_key": "test-api-key",
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 256,
        }
    }
    (config_dir / "model.json").write_text(
        json.dumps(model_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    coordinator_role = """---
role_id: coordinator_agent
name: Coordinator Agent
version: 1.0.0
model_profile: default
depends_on: []
tools: []
---
You are a deterministic coordinator used for integration testing.
Always return concise output.
"""
    (roles_dir / "coordinator_agent.md").write_text(
        coordinator_role,
        encoding="utf-8",
    )
