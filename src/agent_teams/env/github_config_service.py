# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_teams.env.github_config_models import GitHubConfig
from agent_teams.env.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
    GitHubConnectivityProbeService,
)
from agent_teams.env.github_env import (
    GH_TOKEN_ENV_KEY,
    github_env_keys,
    normalize_github_token,
    resolve_github_token_from_env,
)
from agent_teams.env.github_secret_store import (
    GitHubSecretStore,
    get_github_secret_store,
)
from agent_teams.env.proxy_env import ProxyEnvConfig
from agent_teams.env.runtime_env import load_env_file, sync_app_env_to_process_env


class GitHubConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        secret_store: GitHubSecretStore | None = None,
        get_proxy_config: Callable[[], ProxyEnvConfig] | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._secret_store = (
            get_github_secret_store() if secret_store is None else secret_store
        )
        self._probe_service = GitHubConnectivityProbeService(
            get_github_config=self.get_github_config,
            get_proxy_config=(
                (lambda: ProxyEnvConfig())
                if get_proxy_config is None
                else get_proxy_config
            ),
        )

    def get_github_config(self) -> GitHubConfig:
        env_values = load_env_file(self._config_dir / ".env")
        token = self._secret_store.get_token(self._config_dir)
        if token is None:
            token = resolve_github_token_from_env(env_values)
        return GitHubConfig(token=token)

    def save_github_config(self, config: GitHubConfig) -> None:
        normalized_token = normalize_github_token(config.token)
        if self._secret_store.can_persist_token():
            self._write_env_file(token=None)
            self._secret_store.set_token(self._config_dir, normalized_token)
        else:
            self._write_env_file(token=normalized_token)
        sync_app_env_to_process_env(self._config_dir / ".env")

    def probe_connectivity(
        self,
        request: GitHubConnectivityProbeRequest,
    ) -> GitHubConnectivityProbeResult:
        return self._probe_service.probe(request)

    def _write_env_file(self, *, token: str | None) -> None:
        env_file_path = self._config_dir / ".env"
        managed_key_set = set(github_env_keys())
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
            output_lines.append(f"{GH_TOKEN_ENV_KEY}={token}")
        serialized_text = "\n".join(output_lines)
        if serialized_text:
            serialized_text = f"{serialized_text}\n"
        env_file_path.write_text(serialized_text, encoding="utf-8")
