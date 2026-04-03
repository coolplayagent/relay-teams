# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.env.runtime_env import load_env_file, sync_app_env_to_process_env
from agent_teams.env.web_config_models import (
    WebConfig,
    WebFallbackProvider,
    WebProvider,
)
from agent_teams.env.web_secret_store import WebSecretStore, get_web_secret_store
from agent_teams.logger import get_logger

_PROVIDER_ENV_KEY = "AGENT_TEAMS_WEB_PROVIDER"
_FALLBACK_PROVIDER_ENV_KEY = "AGENT_TEAMS_WEB_FALLBACK_PROVIDER"
_SEARXNG_INSTANCE_URL_ENV_KEY = "AGENT_TEAMS_WEB_SEARXNG_INSTANCE_URL"
_LEGACY_API_KEY_ENV_KEY = "AGENT_TEAMS_WEB_API_KEY"
_EXA_API_KEY_ENV_KEY = "AGENT_TEAMS_WEB_EXA_API_KEY"
_SEARXNG_API_KEY_ENV_KEY = "AGENT_TEAMS_WEB_SEARXNG_API_KEY"

LOGGER = get_logger(__name__)


class WebConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        secret_store: WebSecretStore | None = None,
    ) -> None:
        self._config_dir: Path = config_dir
        self._secret_store: WebSecretStore = (
            get_web_secret_store() if secret_store is None else secret_store
        )

    def get_web_config(self) -> WebConfig:
        env_values = load_env_file(self._config_dir / ".env")
        provider, used_legacy_searxng_provider = _parse_provider(
            env_values.get(_PROVIDER_ENV_KEY)
        )
        fallback_provider = _parse_fallback_provider(
            env_values.get(_FALLBACK_PROVIDER_ENV_KEY)
        )
        should_rewrite_env_file = used_legacy_searxng_provider
        if used_legacy_searxng_provider:
            fallback_provider = WebFallbackProvider.SEARXNG
            LOGGER.warning(
                "Normalized legacy web provider 'searxng' to Exa primary with SearXNG fallback"
            )
        searxng_instance_url = _normalize_text(
            env_values.get(_SEARXNG_INSTANCE_URL_ENV_KEY)
        )
        exa_api_key = self._secret_store.get_api_key(self._config_dir, WebProvider.EXA)
        if exa_api_key is None:
            exa_api_key = _normalize_text(
                env_values.get(_EXA_API_KEY_ENV_KEY)
                or env_values.get(_LEGACY_API_KEY_ENV_KEY)
            )
            if exa_api_key is not None:
                self._secret_store.set_api_key(
                    self._config_dir,
                    WebProvider.EXA,
                    exa_api_key,
                )
                should_rewrite_env_file = True
        legacy_searxng_api_key = _normalize_text(
            env_values.get(_SEARXNG_API_KEY_ENV_KEY)
        )
        if legacy_searxng_api_key is not None:
            should_rewrite_env_file = True
            LOGGER.warning(
                "Ignoring deprecated SearXNG web API key because SearXNG now only runs as a fallback provider"
            )
        self._secret_store.delete_api_key(self._config_dir, WebProvider.SEARXNG)
        config = WebConfig(
            provider=provider,
            exa_api_key=exa_api_key,
            fallback_provider=fallback_provider,
            searxng_instance_url=searxng_instance_url,
        )
        if should_rewrite_env_file:
            self._write_env_file(
                provider=config.provider,
                fallback_provider=config.fallback_provider,
                searxng_instance_url=config.searxng_instance_url,
            )
        return config

    def save_web_config(self, config: WebConfig) -> None:
        self._write_env_file(
            provider=config.provider,
            fallback_provider=config.fallback_provider,
            searxng_instance_url=config.searxng_instance_url,
        )
        self._secret_store.set_api_key(
            self._config_dir,
            WebProvider.EXA,
            config.exa_api_key,
        )
        self._secret_store.delete_api_key(self._config_dir, WebProvider.SEARXNG)
        sync_app_env_to_process_env(self._config_dir / ".env")

    def resolve_runtime_config(self) -> WebConfig:
        return self.get_web_config()

    def _write_env_file(
        self,
        *,
        provider: WebProvider,
        fallback_provider: WebFallbackProvider | None,
        searxng_instance_url: str | None,
    ) -> None:
        env_file_path = self._config_dir / ".env"
        managed_values = {
            _PROVIDER_ENV_KEY: provider.value,
            _FALLBACK_PROVIDER_ENV_KEY: (
                fallback_provider.value if fallback_provider is not None else None
            ),
            _SEARXNG_INSTANCE_URL_ENV_KEY: searxng_instance_url,
            _LEGACY_API_KEY_ENV_KEY: None,
            _EXA_API_KEY_ENV_KEY: None,
            _SEARXNG_API_KEY_ENV_KEY: None,
        }
        managed_keys = tuple(managed_values.keys())
        managed_key_set = {key for key in managed_keys}
        written_keys: set[str] = set()
        output_lines: list[str] = []

        existing_lines: list[str] = []
        if env_file_path.exists() and env_file_path.is_file():
            existing_lines = env_file_path.read_text(encoding="utf-8").splitlines()

        for raw_line in existing_lines:
            stripped_line = raw_line.strip()
            if (
                not stripped_line
                or stripped_line.startswith("#")
                or "=" not in raw_line
            ):
                output_lines.append(raw_line)
                continue

            raw_key, _raw_value = raw_line.split("=", 1)
            normalized_key = raw_key.strip().upper()
            if normalized_key not in managed_key_set:
                output_lines.append(raw_line)
                continue

            desired_value = managed_values[normalized_key]
            if desired_value is None or normalized_key in written_keys:
                written_keys.add(normalized_key)
                continue

            output_lines.append(f"{normalized_key}={desired_value}")
            written_keys.add(normalized_key)

        for key in managed_keys:
            value = managed_values[key]
            if value is None or key in written_keys:
                continue
            output_lines.append(f"{key}={value}")

        env_file_path.parent.mkdir(parents=True, exist_ok=True)
        serialized_text = "\n".join(output_lines)
        if serialized_text:
            serialized_text = f"{serialized_text}\n"
        env_file_path.write_text(serialized_text, encoding="utf-8")


def _parse_provider(value: str | None) -> tuple[WebProvider, bool]:
    normalized_value = _normalize_text(value)
    if normalized_value is None:
        return (WebProvider.EXA, False)
    lowered_value = normalized_value.lower()
    if lowered_value == WebProvider.SEARXNG.value:
        return (WebProvider.EXA, True)
    try:
        return (WebProvider(lowered_value), False)
    except ValueError as exc:
        raise ValueError(f"Unsupported web provider: {normalized_value}") from exc


def _parse_fallback_provider(value: str | None) -> WebFallbackProvider | None:
    normalized_value = _normalize_text(value)
    if normalized_value is None:
        return None
    try:
        return WebFallbackProvider(normalized_value.lower())
    except ValueError as exc:
        raise ValueError(
            f"Unsupported web fallback provider: {normalized_value}"
        ) from exc


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
