from __future__ import annotations

import os
from json import loads
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.core.models import ModelEndpointConfig, SamplingConfig


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
    config_dir: Path = Path(".agent_teams"),
    roles_dir: Path | None = None,
    db_path: Path | None = None,
) -> RuntimeConfig:
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    pairs = _parse_env_file(env_file) if env_file.exists() else ()

    resolved_roles_dir = roles_dir or Path(
        _get_value(pairs, "AGENT_TEAMS_ROLES_DIR") or config_dir / "roles"
    )
    resolved_db_path = db_path or _resolve_path(
        config_dir, _get_value(pairs, "AGENT_TEAMS_DB_PATH") or "agent_teams.db"
    )
    llm_profiles = load_llm_configs(config_dir, pairs)

    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=config_dir,
            env_file=env_file,
            db_path=resolved_db_path,
            roles_dir=resolved_roles_dir,
        ),
        llm_profiles=llm_profiles,
    )


def load_llm_configs(
    config_dir: Path,
    env_pairs: tuple[tuple[str, str], ...],
) -> dict[str, ModelEndpointConfig]:
    llm_file = config_dir / "llm.json"
    if not llm_file.exists():
        raise FileNotFoundError(
            f"llm.json not found in {config_dir}. "
            "Please create .agent_teams/llm.json with a 'default' profile."
        )

    try:
        data = loads(llm_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse llm.json: {e}")

    if "default" not in data:
        raise ValueError("llm.json must contain a 'default' profile.")

    profiles: dict[str, ModelEndpointConfig] = {}
    for name, cfg in data.items():
        model = cfg.get("model")
        base_url = cfg.get("base_url")
        api_key = _resolve_env_var(cfg.get("api_key", ""), env_pairs)

        if not model or not base_url or not api_key:
            raise ValueError(
                f"Invalid profile '{name}': missing required fields (model, base_url, api_key)."
            )

        temperature = cfg.get("temperature", 0.2)
        top_p = cfg.get("top_p", 1.0)
        max_tokens = cfg.get("max_tokens", 1024)
        top_k = cfg.get("top_k")

        profiles[name] = ModelEndpointConfig(
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


def _resolve_env_var(value: str, pairs: tuple[tuple[str, str], ...]) -> str:
    if value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        return _get_value(pairs, env_key) or os.environ.get(env_key, value)
    return value


def _parse_env_file(path: Path) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        rows.append((key.strip(), _strip_quotes(value.strip())))
    return tuple(rows)


def _get_value(pairs: tuple[tuple[str, str], ...], key: str) -> str | None:
    for current_key, current_value in reversed(pairs):
        if current_key == key:
            return current_value
    return None


def _resolve_path(config_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return config_dir / candidate


def _strip_quotes(value: str) -> str:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1]
    return value
