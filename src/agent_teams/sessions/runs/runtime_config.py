# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import loads
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.env import load_merged_env_vars
from agent_teams.paths import get_app_config_dir
from agent_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    ModelEndpointConfig,
    ProviderType,
    SamplingConfig,
)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class RuntimePaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_dir: Path
    env_file: Path
    db_path: Path
    roles_dir: Path


class ModelConfigStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loaded: bool
    profiles: tuple[str, ...] = ()
    error: str | None = None


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: RuntimePaths
    llm_profiles: dict[str, ModelEndpointConfig]
    model_status: ModelConfigStatus = ModelConfigStatus(loaded=True)


def load_runtime_config(
    config_dir: Path | None = None,
    roles_dir: Path | None = None,
    db_path: Path | None = None,
) -> RuntimeConfig:
    resolved_config_dir = (
        get_app_config_dir()
        if config_dir is None
        else config_dir.expanduser().resolve()
    )
    resolved_config_dir.mkdir(parents=True, exist_ok=True)

    env_file = resolved_config_dir / ".env"
    merged_env = load_merged_env_vars(extra_env_files=(env_file,))

    resolved_roles_dir = (
        roles_dir.expanduser().resolve()
        if roles_dir is not None
        else resolved_config_dir / "roles"
    )
    resolved_db_path = (
        db_path.expanduser().resolve()
        if db_path is not None
        else resolved_config_dir / "agent_teams.db"
    )
    try:
        llm_profiles = load_llm_configs(resolved_config_dir, merged_env)
        model_status = ModelConfigStatus(
            loaded=True,
            profiles=tuple(sorted(llm_profiles.keys())),
        )
    except (FileNotFoundError, ValueError) as exc:
        llm_profiles = {}
        model_status = ModelConfigStatus(
            loaded=False,
            profiles=(),
            error=str(exc),
        )

    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=resolved_config_dir,
            env_file=env_file,
            db_path=resolved_db_path,
            roles_dir=resolved_roles_dir,
        ),
        llm_profiles=llm_profiles,
        model_status=model_status,
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
        api_key = _resolve_required_config_value(
            cfg.get("api_key", ""),
            env_values,
            profile_name=name,
            field_name="api_key",
        )
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
        ssl_verify = _coerce_optional_ssl_verify(
            cfg.get("ssl_verify"),
            profile_name=name,
        )
        connect_timeout_seconds = cfg.get(
            "connect_timeout_seconds",
            DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
        )

        profiles[name] = ModelEndpointConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            sampling=SamplingConfig(
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                top_k=top_k,
            ),
        )

    return profiles


def _resolve_required_config_value(
    value: str,
    env_values: Mapping[str, str],
    *,
    profile_name: str,
    field_name: str,
) -> str:
    if value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1].strip()
        if not env_key:
            raise ValueError(
                f"Invalid profile '{profile_name}': empty environment variable placeholder for {field_name}."
            )

        resolved_value = env_values.get(env_key)
        if resolved_value is None:
            raise ValueError(
                f"Invalid profile '{profile_name}': environment variable '{env_key}' referenced by {field_name} is not set."
            )
        if not resolved_value:
            raise ValueError(
                f"Invalid profile '{profile_name}': environment variable '{env_key}' referenced by {field_name} is empty."
            )
        return resolved_value
    return value


def _coerce_optional_ssl_verify(value: object, *, profile_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    raise ValueError(
        f"Invalid profile '{profile_name}': ssl_verify must be true, false, or null."
    )


def _resolve_path(config_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return config_dir / candidate
