# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_auth import clear_clawhub_runtime_home
from relay_teams.env.clawhub_env import (
    CLAWHUB_TOKEN_ENV_KEY,
    clawhub_env_keys,
    normalize_clawhub_token,
    resolve_clawhub_token_from_env,
)
from relay_teams.env.clawhub_secret_store import (
    ClawHubSecretStore,
    get_clawhub_secret_store,
)
from relay_teams.env.runtime_env import load_env_file, sync_app_env_to_process_env


class ClawHubConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        secret_store: ClawHubSecretStore | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._secret_store = (
            get_clawhub_secret_store() if secret_store is None else secret_store
        )

    def get_clawhub_config(self) -> ClawHubConfig:
        env_values = load_env_file(self._config_dir / ".env")
        token = self._secret_store.get_token(self._config_dir)
        if token is None:
            token = resolve_clawhub_token_from_env(env_values)
            if token is not None:
                self._secret_store.set_token(self._config_dir, token)
                self._write_env_file(token=None)
        return ClawHubConfig(token=token)

    def save_clawhub_config(self, config: ClawHubConfig) -> None:
        normalized_token = normalize_clawhub_token(config.token)
        self._write_env_file(token=None)
        self._secret_store.set_token(self._config_dir, normalized_token)
        if normalized_token is None:
            clear_clawhub_runtime_home(self._config_dir)
        sync_app_env_to_process_env(self._config_dir / ".env")

    def _write_env_file(self, *, token: str | None) -> None:
        env_file_path = self._config_dir / ".env"
        managed_key_set = set(clawhub_env_keys())
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
            if normalized_key in managed_key_set:
                continue
            output_lines.append(raw_line)

        env_file_path.parent.mkdir(parents=True, exist_ok=True)
        if token is not None:
            output_lines.append(f"{CLAWHUB_TOKEN_ENV_KEY}={token}")
        serialized_text = "\n".join(output_lines)
        if serialized_text:
            serialized_text = f"{serialized_text}\n"
        env_file_path.write_text(serialized_text, encoding="utf-8")
