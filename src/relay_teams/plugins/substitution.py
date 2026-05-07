# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import re

from pydantic import JsonValue

_PLUGIN_VAR_PATTERN = re.compile(r"\$\{(?P<name>[^}]+)}")


def substitute_plugin_vars(
    *,
    value: JsonValue,
    plugin_root: Path,
    plugin_data: Path,
    user_config: dict[str, JsonValue],
    allow_env: bool = False,
) -> JsonValue:
    if isinstance(value, str):
        return _PLUGIN_VAR_PATTERN.sub(
            lambda match: _plugin_var_value(
                name=match.group("name"),
                plugin_root=plugin_root,
                plugin_data=plugin_data,
                user_config=user_config,
                allow_env=allow_env,
            ),
            value,
        )
    if isinstance(value, list):
        return [
            substitute_plugin_vars(
                value=item,
                plugin_root=plugin_root,
                plugin_data=plugin_data,
                user_config=user_config,
                allow_env=allow_env,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: substitute_plugin_vars(
                value=item,
                plugin_root=plugin_root,
                plugin_data=plugin_data,
                user_config=user_config,
                allow_env=allow_env,
            )
            for key, item in value.items()
        }
    return value


def _plugin_var_value(
    *,
    name: str,
    plugin_root: Path,
    plugin_data: Path,
    user_config: dict[str, JsonValue],
    allow_env: bool,
) -> str:
    if name in {"plugin_root", "RELAY_TEAMS_PLUGIN_ROOT"}:
        return str(plugin_root)
    if name in {"plugin_data", "RELAY_TEAMS_PLUGIN_DATA"}:
        return str(plugin_data)
    if name.startswith("env:"):
        if not allow_env:
            return ""
        return os.environ.get(name.removeprefix("env:"), "")
    if name.startswith("user_config."):
        raw_value = user_config.get(name.removeprefix("user_config."))
        if isinstance(raw_value, (str, int, float, bool)):
            return str(raw_value)
    return ""
