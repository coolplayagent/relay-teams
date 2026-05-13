# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

_OPENCLAW_ADAPTER_NAME = "openclaw"
_OPENCLAW_MANIFEST = "openclaw.plugin.json"


def adapt_openclaw_plugin_tree(
    *,
    plugin_root: Path,
    adapter: str,
    manifest_config_dir_name: str,
    source_version: str | None = None,
) -> None:
    if adapter != _OPENCLAW_ADAPTER_NAME:
        return
    relay_manifest_path = plugin_root / manifest_config_dir_name / "plugin.json"
    claude_manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    if relay_manifest_path.exists() or claude_manifest_path.exists():
        return
    openclaw_manifest_path = plugin_root / _OPENCLAW_MANIFEST
    if not openclaw_manifest_path.exists():
        manifest = _relay_manifest_from_static_roots(
            plugin_root=plugin_root,
            source_version=source_version,
        )
        if not _has_mappable_manifest_components(manifest):
            return
        relay_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        relay_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return
    raw_manifest = json.loads(openclaw_manifest_path.read_text(encoding="utf-8-sig"))
    manifest = _relay_manifest_from_openclaw(
        raw_manifest,
        plugin_root=plugin_root,
        source_version=source_version,
    )
    if _native_only_openclaw_plugin(raw_manifest=raw_manifest, manifest=manifest):
        raise ValueError(
            "OpenClaw native runtime extension plugins are not supported unless "
            "they also include Relay Teams mappable components"
        )
    relay_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    relay_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _relay_manifest_from_static_roots(
    *,
    plugin_root: Path,
    source_version: str | None,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "name": _safe_plugin_name(plugin_root.name),
        "version": _static_manifest_version(source_version),
        "description": "",
    }
    _add_static_component_paths(manifest=manifest, plugin_root=plugin_root)
    return manifest


def _static_manifest_version(source_version: str | None) -> str:
    if source_version is None:
        return "local"
    normalized = source_version.strip()
    return normalized or "local"


def _relay_manifest_from_openclaw(
    raw_manifest: object,
    *,
    plugin_root: Path,
    source_version: str | None,
) -> dict[str, object]:
    raw = _string_key_mapping(raw_manifest)
    name = _safe_plugin_name(
        _string_field(raw, "id")
        or _string_field(raw, "name")
        or _string_field(raw, "runtimeId")
        or plugin_root.name
    )
    manifest: dict[str, object] = {
        "name": name,
        "version": _string_field(raw, "version")
        or _static_manifest_version(source_version),
        "description": _string_field(raw, "description")
        or _string_field(raw, "displayName"),
    }
    _add_static_component_paths(manifest=manifest, plugin_root=plugin_root)
    user_config = _user_config_from_openclaw(raw.get("configSchema"))
    if user_config:
        manifest["user_config"] = user_config
    return manifest


def _add_static_component_paths(
    *,
    manifest: dict[str, object],
    plugin_root: Path,
) -> None:
    if (plugin_root / "skills").is_dir():
        manifest["skills"] = "./skills"
    if (plugin_root / "agents").is_dir():
        manifest["roles"] = "./agents"
    elif (plugin_root / "roles").is_dir():
        manifest["roles"] = "./roles"
    if (plugin_root / "commands").is_dir():
        manifest["commands"] = "./commands"
    if (plugin_root / ".mcp.json").is_file():
        manifest["mcp_servers"] = "./.mcp.json"
    elif (plugin_root / "mcp.json").is_file():
        manifest["mcp_servers"] = "./mcp.json"
    if (plugin_root / "hooks" / "hooks.json").is_file():
        manifest["hooks"] = "./hooks/hooks.json"


def _has_mappable_manifest_components(manifest: dict[str, object]) -> bool:
    return any(
        key in manifest
        for key in {"skills", "roles", "commands", "mcp_servers", "hooks"}
    )


def _native_only_openclaw_plugin(
    *,
    raw_manifest: object,
    manifest: dict[str, object],
) -> bool:
    raw = _string_key_mapping(raw_manifest)
    runtime_extensions = raw.get("runtimeExtensions")
    has_runtime_extensions = (
        isinstance(runtime_extensions, list) and len(runtime_extensions) > 0
    )
    if not has_runtime_extensions:
        return False
    mappable_keys = {"skills", "roles", "commands", "mcp_servers", "hooks"}
    return not any(key in manifest for key in mappable_keys)


def _user_config_from_openclaw(value: object) -> dict[str, object]:
    schema = _string_key_mapping(value)
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    required = schema.get("required")
    required_names = (
        {item for item in required if isinstance(item, str)}
        if isinstance(required, list)
        else set()
    )
    fields: dict[str, object] = {}
    for key, raw_field in properties.items():
        field = _string_key_mapping(raw_field)
        field_name = str(key)
        if not field_name.strip():
            continue
        fields[field_name] = {
            "type": _string_field(field, "type") or "string",
            "title": _string_field(field, "title"),
            "description": _string_field(field, "description"),
            "default": field.get("default"),
            "sensitive": _bool_field(field, "sensitive"),
            "required": field_name in required_names,
        }
    return fields


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _string_field(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    return item.strip() if isinstance(item, str) else ""


def _bool_field(value: dict[str, object], key: str) -> bool:
    item = value.get(key)
    return item is True


def _safe_plugin_name(value: str) -> str:
    normalized = value.strip().replace("@", "").replace("/", "-")
    safe = "".join(
        char if char.isalnum() or char in {"_", "-"} else "-" for char in normalized
    )
    safe = safe.strip("-_")
    return safe or "openclaw-plugin"
