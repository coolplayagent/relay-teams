# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

from agent_teams.logger import get_logger
from agent_teams.mcp.registry import McpRegistry, McpServerSpec
from agent_teams.notifications import NotificationConfig, default_notification_config
from agent_teams.shared_types.json_types import JsonObject, JsonValue

logger = get_logger(__name__)


class ConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir: Path = config_dir

    def get_model_config(self) -> JsonObject:
        model_file = self._config_dir / "model.json"
        if model_file.exists():
            return _load_json_object(model_file)
        return {}

    def get_model_profiles(self) -> dict[str, JsonObject]:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return {}
        config = _load_json_object(model_file)
        result: dict[str, JsonObject] = {}
        for name, profile in config.items():
            if not isinstance(profile, dict):
                continue
            result[name] = {
                "provider": profile.get("provider", "openai_compatible"),
                "model": profile.get("model", ""),
                "base_url": profile.get("base_url", ""),
                "has_api_key": bool(profile.get("api_key")),
                "temperature": profile.get("temperature", 0.7),
                "top_p": profile.get("top_p", 1.0),
                "max_tokens": profile.get("max_tokens", 4096),
            }
        return result

    def save_model_profile(self, name: str, profile: JsonObject) -> None:
        model_file = self._config_dir / "model.json"
        config: JsonObject = {}
        if model_file.exists():
            config = _load_json_object(model_file)
        config[name] = profile
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def delete_model_profile(self, name: str) -> None:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return
        config = _load_json_object(model_file)
        if name in config:
            del config[name]
            _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def save_model_config(self, config: JsonObject) -> None:
        model_file = self._config_dir / "model.json"
        _ = model_file.write_text(dumps(config, indent=2), encoding="utf-8")

    def get_notification_config(self) -> NotificationConfig:
        notification_file = self._config_dir / "notifications.json"
        if not notification_file.exists():
            return default_notification_config()
        try:
            payload = _load_json_object(notification_file)
            return NotificationConfig.model_validate(payload)
        except Exception:
            return default_notification_config()

    def save_notification_config(self, config: NotificationConfig) -> None:
        notification_file = self._config_dir / "notifications.json"
        _ = notification_file.write_text(
            dumps(config.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def load_mcp_registry(self) -> McpRegistry:
        mcp_specs: list[McpServerSpec] = []
        mcp_file = self._config_dir / "mcp.json"
        if mcp_file.exists():
            try:
                mcp_data = _load_json_object(mcp_file)
                maybe_servers = mcp_data.get("mcpServers", mcp_data)
                if not isinstance(maybe_servers, dict):
                    maybe_servers = {}
                for name, cfg in maybe_servers.items():
                    wrapped_cfg: JsonObject = {
                        "mcpServers": {name: _normalize_json_value(cfg)},
                    }
                    mcp_specs.append(McpServerSpec(name=name, config=wrapped_cfg))
            except Exception as exc:
                logger.warning("Failed to load mcp.json: %s", exc)
        return McpRegistry(tuple(mcp_specs))


def _load_json_object(file_path: Path) -> JsonObject:
    raw = cast(object, loads(file_path.read_text("utf-8")))
    if isinstance(raw, dict):
        return cast(JsonObject, raw)
    return {}


def _normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_normalize_json_value(item) for item in items]
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        normalized: JsonObject = {}
        for key, item in entries.items():
            key_str: str = str(key)
            normalized[key_str] = _normalize_json_value(item)
        return normalized
    return str(value)
