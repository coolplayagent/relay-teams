# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_teams.env.proxy_env import (
    ProxyEnvConfig,
    ProxyEnvInput,
    apply_proxy_password,
    mask_proxy_url,
    proxy_config_contains_password,
    resolve_proxy_env_config,
    sanitize_proxy_config_for_storage,
)
from agent_teams.env.proxy_secret_store import ProxySecretStore, get_proxy_secret_store
from agent_teams.env.runtime_env import load_env_file, load_merged_env_vars
from agent_teams.env.web_connectivity import (
    WebConnectivityProbeRequest,
    WebConnectivityProbeResult,
    WebConnectivityProbeService,
)
from agent_teams.shared_types.json_types import JsonObject


class ProxyConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        on_proxy_reloaded: Callable[[ProxyEnvConfig], None],
        secret_store: ProxySecretStore | None = None,
    ) -> None:
        self._config_dir: Path = config_dir
        self._on_proxy_reloaded: Callable[[ProxyEnvConfig], None] = on_proxy_reloaded
        self._secret_store: ProxySecretStore = (
            get_proxy_secret_store() if secret_store is None else secret_store
        )
        self._web_probe_service = WebConnectivityProbeService(
            get_proxy_config=self.get_proxy_config
        )

    def get_saved_proxy_config(self) -> ProxyEnvInput:
        env_values = load_env_file(self._config_dir / ".env")
        proxy_input = ProxyEnvInput.from_config(resolve_proxy_env_config(env_values))
        if (
            proxy_input.proxy_password is None
            and proxy_input.proxy_username is not None
        ):
            proxy_input.proxy_password = self._secret_store.get_password(
                self._config_dir
            )
        return proxy_input

    def get_proxy_config(self) -> ProxyEnvConfig:
        proxy_config = resolve_proxy_env_config(
            load_merged_env_vars(extra_env_files=(self._config_dir / ".env",))
        )
        if proxy_config_contains_password(proxy_config):
            return proxy_config

        proxy_password = self._secret_store.get_password(self._config_dir)
        if proxy_password is None:
            return proxy_config
        return apply_proxy_password(proxy_config, password=proxy_password)

    def get_proxy_status(self) -> JsonObject:
        config = self.get_proxy_config()
        return {
            "loaded": True,
            "has_proxy": config.has_proxy,
            "http_proxy": mask_proxy_url(config.http_proxy),
            "https_proxy": mask_proxy_url(config.https_proxy),
            "all_proxy": mask_proxy_url(config.all_proxy),
            "no_proxy": config.no_proxy,
        }

    def reload_proxy_config(self) -> None:
        self._on_proxy_reloaded(self.get_proxy_config())

    def save_proxy_config(self, payload: ProxyEnvInput) -> None:
        payload_config = payload.to_config()
        resolved_input = ProxyEnvInput.from_config(payload_config)
        if (
            proxy_config_contains_password(payload_config)
            and resolved_input.proxy_password is None
        ):
            raise ValueError(
                "Saving multiple distinct proxy passwords is not supported. "
                "Use one shared username/password pair, or keep per-proxy passwords only in manual .env values."
            )
        proxy_password = resolved_input.proxy_password
        sanitized_input = resolved_input.model_copy(
            update={"proxy_password": None},
            deep=True,
        )
        proxy_config = sanitize_proxy_config_for_storage(sanitized_input.to_config())
        if proxy_config_contains_password(proxy_config):
            raise ValueError(
                "Proxy passwords cannot be saved to .env. "
                "Use the dedicated username/password fields with a shared credential set."
            )

        if proxy_password is not None:
            self._secret_store.set_password(self._config_dir, proxy_password)
        else:
            self._secret_store.delete_password(self._config_dir)

        self._write_proxy_env_file(proxy_config)
        self._on_proxy_reloaded(self.get_proxy_config())

    def probe_web_connectivity(
        self,
        request: WebConnectivityProbeRequest,
    ) -> WebConnectivityProbeResult:
        return self._web_probe_service.probe(request)

    def _write_proxy_env_file(self, proxy_config: ProxyEnvConfig) -> None:
        env_file_path = self._config_dir / ".env"
        managed_values = {
            "HTTP_PROXY": proxy_config.http_proxy,
            "HTTPS_PROXY": proxy_config.https_proxy,
            "ALL_PROXY": proxy_config.all_proxy,
            "NO_PROXY": proxy_config.no_proxy,
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

            output_lines.append(
                f"{normalized_key}={_serialize_env_value(desired_value)}"
            )
            written_keys.add(normalized_key)

        for key in managed_keys:
            value = managed_values[key]
            if value is None or key in written_keys:
                continue
            output_lines.append(f"{key}={_serialize_env_value(value)}")

        env_file_path.parent.mkdir(parents=True, exist_ok=True)
        serialized_text = "\n".join(output_lines)
        if serialized_text:
            serialized_text = f"{serialized_text}\n"
        env_file_path.write_text(serialized_text, encoding="utf-8")


def _serialize_env_value(value: str) -> str:
    if any(character.isspace() for character in value) or "#" in value:
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped_value}"'
    return value
