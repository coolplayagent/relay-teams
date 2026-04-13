# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from relay_teams.env.github_config_models import (
    GitHubConfig,
    GitHubConfigUpdate,
    GitHubConfigView,
)
from relay_teams.env.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
    GitHubConnectivityProbeService,
    GitHubWebhookConnectivityProbeRequest,
    GitHubWebhookConnectivityProbeResult,
    GitHubWebhookConnectivityProbeService,
)
from relay_teams.env.github_env import (
    GH_TOKEN_ENV_KEY,
    github_env_keys,
    normalize_github_token,
    resolve_github_token_from_env,
)
from relay_teams.env.github_secret_store import (
    GitHubSecretStore,
    get_github_secret_store,
)
from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.env.runtime_env import load_env_file, sync_app_env_to_process_env
from relay_teams.logger import get_logger

_WEBHOOK_BASE_URL_ENV_KEY = "AGENT_TEAMS_GITHUB_WEBHOOK_BASE_URL"
LOGGER = get_logger(__name__)


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
        self._webhook_probe_service = GitHubWebhookConnectivityProbeService(
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
        webhook_base_url = env_values.get(_WEBHOOK_BASE_URL_ENV_KEY)
        should_rewrite_env_file = False
        if token is None:
            token = resolve_github_token_from_env(env_values)
            if token is not None:
                self._secret_store.set_token(self._config_dir, token)
                should_rewrite_env_file = True
        try:
            config = GitHubConfig(token=token, webhook_base_url=webhook_base_url)
        except ValueError:
            if webhook_base_url is None:
                raise
            LOGGER.warning("Ignoring invalid GitHub webhook base URL from saved config")
            config = GitHubConfig(token=token, webhook_base_url=None)
            should_rewrite_env_file = True
        if should_rewrite_env_file:
            self._write_env_file(
                token=None,
                webhook_base_url=config.webhook_base_url,
            )
        return config

    def get_github_config_view(self) -> GitHubConfigView:
        config = self.get_github_config()
        return GitHubConfigView(
            token_configured=config.token is not None,
            webhook_base_url=config.webhook_base_url,
        )

    def save_github_config(self, config: GitHubConfig) -> None:
        normalized_token = normalize_github_token(config.token)
        self._write_env_file(
            token=None,
            webhook_base_url=config.webhook_base_url,
        )
        self._secret_store.set_token(self._config_dir, normalized_token)
        sync_app_env_to_process_env(self._config_dir / ".env")

    def update_github_config(self, request: GitHubConfigUpdate) -> GitHubConfig:
        existing = self.get_github_config()
        next_token = existing.token
        provided_token = normalize_github_token(request.token)
        if provided_token is not None:
            next_token = provided_token

        webhook_base_url = (
            request.webhook_base_url
            if "webhook_base_url" in request.model_fields_set
            else existing.webhook_base_url
        )
        next_config = GitHubConfig(
            token=next_token,
            webhook_base_url=webhook_base_url,
        )
        self.save_github_config(next_config)
        return next_config

    def probe_connectivity(
        self,
        request: GitHubConnectivityProbeRequest,
    ) -> GitHubConnectivityProbeResult:
        return self._probe_service.probe(request)

    def probe_webhook_connectivity(
        self,
        request: GitHubWebhookConnectivityProbeRequest,
    ) -> GitHubWebhookConnectivityProbeResult:
        return self._webhook_probe_service.probe(request)

    def _write_env_file(
        self,
        *,
        token: str | None,
        webhook_base_url: str | None,
    ) -> None:
        env_file_path = self._config_dir / ".env"
        managed_key_set = set(github_env_keys())
        managed_key_set.add(_WEBHOOK_BASE_URL_ENV_KEY)
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
        if webhook_base_url is not None:
            output_lines.append(f"{_WEBHOOK_BASE_URL_ENV_KEY}={webhook_base_url}")
        serialized_text = "\n".join(output_lines)
        if serialized_text:
            serialized_text = f"{serialized_text}\n"
        env_file_path.write_text(serialized_text, encoding="utf-8")
