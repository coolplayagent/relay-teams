# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def resolve_plugin_component_path(*, plugin_root: Path, raw_path: str) -> Path:
    normalized = raw_path.strip()
    if not normalized:
        raise ValueError("Plugin component path must not be empty")
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError("Plugin component paths must be relative")
    if normalized.startswith("../") or normalized == "..":
        raise ValueError("Plugin component paths must not traverse outside the plugin")
    if normalized != "." and not normalized.startswith("./"):
        raise ValueError("Plugin component paths must start with ./")
    resolved_root = plugin_root.expanduser().resolve()
    resolved_path = (resolved_root / candidate).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Plugin component path escapes the plugin root") from exc
    return resolved_path


def namespace_plugin_ref(*, plugin_name: str, local_name: str) -> str:
    normalized_plugin = plugin_name.strip()
    normalized_local = local_name.strip()
    if not normalized_plugin:
        raise ValueError("plugin_name must not be empty")
    if not normalized_local:
        raise ValueError("local_name must not be empty")
    prefix, separator, _ = normalized_local.partition(":")
    if separator == ":" and prefix == normalized_plugin:
        return normalized_local
    return f"{normalized_plugin}:{normalized_local}"
