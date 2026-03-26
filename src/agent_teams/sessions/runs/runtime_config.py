# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import loads
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_teams.agents.execution.prompt_instructions import (
    PromptInstructionsConfig,
    load_prompt_instructions_config,
)
from agent_teams.env import load_merged_env_vars
from agent_teams.paths import get_app_config_dir
from agent_teams.providers.model_config import (
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    LlmRetryConfig,
    ModelEndpointConfig,
    ProviderType,
    SamplingConfig,
)
from agent_teams.secrets import get_secret_store

_MODEL_PROFILE_SECRET_NAMESPACE = "model_profile"
_MODEL_PROFILE_SECRET_FIELD = "api_key"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class RuntimePaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_dir: Path
    env_file: Path
    db_path: Path
    roles_dir: Path
    prompts_file: Path | None = None

    @model_validator(mode="after")
    def _default_prompts_file(self) -> RuntimePaths:
        if self.prompts_file is None:
            self.prompts_file = self.config_dir / "prompts.json"
        return self


class ModelConfigStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loaded: bool
    profiles: tuple[str, ...] = ()
    error: str | None = None


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: RuntimePaths
    llm_profiles: dict[str, ModelEndpointConfig]
    llm_retry: LlmRetryConfig = Field(default_factory=LlmRetryConfig)
    default_model_profile: str | None = None
    model_status: ModelConfigStatus = ModelConfigStatus(loaded=True)
    prompt_instructions: PromptInstructionsConfig = Field(
        default_factory=PromptInstructionsConfig
    )


class LoadedLlmProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: dict[str, ModelEndpointConfig]
    default_profile_name: str


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
    prompts_file = resolved_config_dir / "prompts.json"
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
        loaded_profiles = load_llm_profile_state(resolved_config_dir, merged_env)
        llm_profiles = loaded_profiles.profiles
        model_status = ModelConfigStatus(
            loaded=True,
            profiles=tuple(sorted(llm_profiles.keys())),
        )
    except (FileNotFoundError, ValueError) as exc:
        llm_profiles = {}
        default_model_profile = None
        model_status = ModelConfigStatus(
            loaded=False,
            profiles=(),
            error=str(exc),
        )
    else:
        default_model_profile = loaded_profiles.default_profile_name
    prompt_instructions = load_prompt_instructions_config(resolved_config_dir)
    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=resolved_config_dir,
            env_file=env_file,
            db_path=resolved_db_path,
            roles_dir=resolved_roles_dir,
            prompts_file=prompts_file,
        ),
        llm_profiles=llm_profiles,
        llm_retry=LlmRetryConfig(),
        default_model_profile=default_model_profile,
        model_status=model_status,
        prompt_instructions=prompt_instructions,
    )


def load_llm_configs(
    config_dir: Path,
    env_values: Mapping[str, str],
) -> dict[str, ModelEndpointConfig]:
    return load_llm_profile_state(config_dir, env_values).profiles


def load_llm_profile_state(
    config_dir: Path,
    env_values: Mapping[str, str],
) -> LoadedLlmProfiles:
    model_file = config_dir / "model.json"
    if not model_file.exists():
        raise FileNotFoundError(
            f"model.json not found in {config_dir}. "
            "Please create model.json with at least one profile."
        )

    data = _load_model_payload(model_file)
    default_profile_name = _resolve_default_profile_name(data)

    profiles: dict[str, ModelEndpointConfig] = {}
    for name, cfg in data.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Invalid profile '{name}': expected an object.")

        model = cfg.get("model")
        base_url = cfg.get("base_url")
        api_key = _resolve_profile_api_key(
            config_dir=config_dir,
            profile_name=name,
            raw_value=cfg.get("api_key"),
            env_values=env_values,
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
        context_window_raw = cfg.get("context_window")
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
            context_window=(
                int(context_window_raw)
                if isinstance(context_window_raw, int) and context_window_raw > 0
                else None
            ),
            connect_timeout_seconds=connect_timeout_seconds,
            sampling=SamplingConfig(
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                top_k=top_k,
            ),
        )

    return LoadedLlmProfiles(
        profiles=profiles,
        default_profile_name=default_profile_name,
    )


def _load_model_payload(model_file: Path) -> dict[str, object]:
    try:
        raw = loads(model_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse model.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("model.json must be a JSON object.")
    return {str(name): value for name, value in raw.items()}


def _resolve_default_profile_name(profile_payloads: Mapping[str, object]) -> str:
    profile_names: list[str] = []
    explicit_defaults: list[str] = []

    for name, cfg in profile_payloads.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Invalid profile '{name}': expected an object.")
        profile_names.append(name)
        is_default = cfg.get("is_default")
        if is_default is True:
            explicit_defaults.append(name)
            continue
        if is_default not in (False, None):
            raise ValueError(
                f"Invalid profile '{name}': is_default must be true, false, or omitted."
            )

    if not profile_names:
        raise ValueError("model.json must contain at least one profile.")
    if len(explicit_defaults) > 1:
        joined_names = ", ".join(sorted(explicit_defaults))
        raise ValueError(
            "model.json must not mark more than one default profile. "
            f"Found: {joined_names}."
        )
    if explicit_defaults:
        return explicit_defaults[0]
    if "default" in profile_payloads:
        return "default"
    if len(profile_names) == 1:
        return profile_names[0]
    return sorted(profile_names)[0]


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


def _resolve_profile_api_key(
    *,
    config_dir: Path,
    profile_name: str,
    raw_value: object,
    env_values: Mapping[str, str],
) -> str:
    if isinstance(raw_value, str) and raw_value.strip():
        return _resolve_required_config_value(
            raw_value.strip(),
            env_values,
            profile_name=profile_name,
            field_name="api_key",
        )
    secret_value = get_secret_store().get_secret(
        config_dir,
        namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
        owner_id=profile_name,
        field_name=_MODEL_PROFILE_SECRET_FIELD,
    )
    if secret_value is None:
        return ""
    return secret_value


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
