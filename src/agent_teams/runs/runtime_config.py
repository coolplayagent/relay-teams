# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import loads
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.env import load_merged_env_vars
from agent_teams.paths import get_project_config_dir
from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderType,
    SamplingConfig,
)


class RuntimePaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_dir: Path
    env_file: Path
    db_path: Path
    roles_dir: Path


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: RuntimePaths
    llm_profiles: dict[str, ModelEndpointConfig]


def load_runtime_config(
    config_dir: Path | None = None,
    roles_dir: Path | None = None,
    db_path: Path | None = None,
) -> RuntimeConfig:
    resolved_config_dir = (
        get_project_config_dir()
        if config_dir is None
        else config_dir.expanduser().resolve()
    )
    resolved_config_dir.mkdir(parents=True, exist_ok=True)

    env_file = resolved_config_dir / ".env"
    merged_env = load_merged_env_vars(extra_env_files=(env_file,))

    resolved_roles_dir = _resolve_path(
        resolved_config_dir,
        str(roles_dir or merged_env.get("AGENT_TEAMS_ROLES_DIR", "roles")),
    )
    resolved_db_path = db_path or _resolve_path(
        resolved_config_dir,
        merged_env.get("AGENT_TEAMS_DB_PATH", "agent_teams.db"),
    )
    llm_profiles = load_llm_configs(resolved_config_dir, merged_env)

    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=resolved_config_dir,
            env_file=env_file,
            db_path=resolved_db_path,
            roles_dir=resolved_roles_dir,
        ),
        llm_profiles=llm_profiles,
    )


def load_llm_configs(
    config_dir: Path,
    env_values: Mapping[str, str],
) -> dict[str, ModelEndpointConfig]:
    model_file = config_dir / "model.json"
    if not model_file.exists():
        raise FileNotFoundError(
            f"model.json not found in {config_dir}. "
            "Please create model.json with a 'default' profile."
        )

    try:
        data = loads(model_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse model.json: {e}")

    if "default" not in data:
        raise ValueError("model.json must contain a 'default' profile.")

    profiles: dict[str, ModelEndpointConfig] = {}
    for name, cfg in data.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Invalid profile '{name}': expected an object.")

        model = cfg.get("model")
        base_url = cfg.get("base_url")
        api_key = _resolve_env_var(cfg.get("api_key", ""), env_values)
        provider_raw = cfg.get("provider", ProviderType.OPENAI_COMPATIBLE.value)
        provider = ProviderType(provider_raw)

        if not model or not base_url or not api_key:
            raise ValueError(
                f"Invalid profile '{name}': missing required fields (model, base_url, api_key)."
            )

        temperature = cfg.get("temperature", 0.2)
        top_p = cfg.get("top_p", 1.0)
        max_tokens = cfg.get("max_tokens", 1024)
        top_k = cfg.get("top_k")

        profiles[name] = ModelEndpointConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            sampling=SamplingConfig(
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                top_k=top_k,
            ),
        )

    return profiles


def _resolve_env_var(value: str, env_values: Mapping[str, str]) -> str:
    if value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        return env_values.get(env_key, value)
    return value


def _resolve_path(config_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return config_dir / candidate
