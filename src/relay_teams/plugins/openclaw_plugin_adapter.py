# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from relay_teams.plugins.claude_plugin_adapter import (
    adapt_agent_role_files,
    adapt_markdown_front_matter_files,
)
from relay_teams.plugins.plugin_models import PluginManifest, PluginUserConfigField

_OPENCLAW_ADAPTER_NAME = "openclaw"
_OPENCLAW_MANIFEST = "openclaw.plugin.json"
_RELAY_MANIFEST_ALIAS_FIELDS = frozenset(
    {
        "$schema",
        "agents",
        "mcpServers",
        "userConfig",
    }
)
_RELAY_COMPONENT_PATH_FIELDS = frozenset(
    {
        "skills",
        "roles",
        "agents",
        "commands",
        "hooks",
        "mcp_servers",
        "mcpServers",
        "monitors",
        "settings",
    }
)
_IGNORED_STATIC_COMPONENT_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
    }
)
_MAX_NESTED_SKILL_DEPTH = 4


def adapt_openclaw_plugin_tree(
    *,
    plugin_root: Path,
    adapter: str,
    manifest_config_dir_name: str,
    source_version: str | None = None,
) -> None:
    if adapter != _OPENCLAW_ADAPTER_NAME:
        return
    adapt_agent_role_files(plugin_root=plugin_root)
    adapt_markdown_front_matter_files(plugin_root=plugin_root)
    relay_manifest_path = plugin_root / manifest_config_dir_name / "plugin.json"
    claude_manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    if relay_manifest_path.exists():
        _sanitize_existing_relay_manifest(
            manifest_path=relay_manifest_path,
            plugin_root=plugin_root,
        )
        return
    if claude_manifest_path.exists():
        _sanitize_existing_relay_manifest(
            manifest_path=claude_manifest_path,
            plugin_root=plugin_root,
        )
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


def _sanitize_existing_relay_manifest(
    *, manifest_path: Path, plugin_root: Path
) -> None:
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    raw = _string_key_mapping(raw_manifest)
    if not raw:
        return
    allowed_fields = set(PluginManifest.model_fields) | _RELAY_MANIFEST_ALIAS_FIELDS
    manifest = {
        key: _sanitize_relay_manifest_value(
            key=key,
            value=value,
            plugin_root=plugin_root,
        )
        for key, value in raw.items()
        if key in allowed_fields
    }
    if manifest == raw:
        return
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sanitize_relay_manifest_value(
    *,
    key: str,
    value: object,
    plugin_root: Path,
) -> object:
    if key == "name" and isinstance(value, str):
        return _safe_plugin_name(value)
    if key in _RELAY_COMPONENT_PATH_FIELDS:
        return _sanitize_component_path_value(
            key=key,
            value=value,
            plugin_root=plugin_root,
        )
    if key not in {"user_config", "userConfig"}:
        return value
    user_config = _string_key_mapping(value)
    if not user_config:
        return value
    allowed_fields = set(PluginUserConfigField.model_fields)
    sanitized: dict[str, object] = {}
    for field_name, raw_field in user_config.items():
        field = _string_key_mapping(raw_field)
        if not field:
            sanitized[field_name] = raw_field
            continue
        sanitized[field_name] = {
            item_key: item_value
            for item_key, item_value in field.items()
            if item_key in allowed_fields
        }
    return sanitized


def _sanitize_component_path_value(
    *,
    key: str,
    value: object,
    plugin_root: Path,
) -> object:
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized or normalized.startswith(("./", "../")):
            return _directory_component_path(
                key=key,
                value=value,
                plugin_root=plugin_root,
            )
        if normalized.startswith(("/", "\\")):
            return value
        return _directory_component_path(
            key=key,
            value=f"./{normalized}",
            plugin_root=plugin_root,
        )
    if isinstance(value, list | tuple):
        sanitized_items: list[object] = []
        seen_paths: set[str] = set()
        for item in value:
            sanitized_item = (
                _sanitize_component_path_value(
                    key=key,
                    value=item,
                    plugin_root=plugin_root,
                )
                if isinstance(item, str)
                else item
            )
            if isinstance(sanitized_item, str):
                if sanitized_item in seen_paths:
                    continue
                seen_paths.add(sanitized_item)
            sanitized_items.append(sanitized_item)
        return sanitized_items
    return value


def _directory_component_path(*, key: str, value: str, plugin_root: Path) -> str:
    if key not in {"skills", "roles", "agents", "commands"}:
        return value
    normalized = value.strip()
    if not normalized:
        return value
    candidate = (plugin_root / normalized).resolve()
    try:
        candidate.relative_to(plugin_root.resolve())
    except ValueError:
        return value
    if candidate.is_file():
        parent = candidate.parent
        if parent == plugin_root.resolve():
            return value
        return "./" + parent.relative_to(plugin_root.resolve()).as_posix()
    return value


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
    else:
        nested_skill_paths = _nested_skill_component_paths(plugin_root)
        if len(nested_skill_paths) == 1:
            manifest["skills"] = nested_skill_paths[0]
        elif nested_skill_paths:
            manifest["skills"] = nested_skill_paths
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


def _nested_skill_component_paths(plugin_root: Path) -> tuple[str, ...]:
    skill_paths: list[str] = []
    for manifest_path in sorted(plugin_root.rglob("SKILL.md")):
        try:
            relative_parent = manifest_path.parent.relative_to(plugin_root)
        except ValueError:
            continue
        if any(
            part in _IGNORED_STATIC_COMPONENT_DIRS for part in relative_parent.parts
        ):
            continue
        if len(relative_parent.parts) > _MAX_NESTED_SKILL_DEPTH:
            continue
        if not relative_parent.parts:
            skill_paths.append(".")
            continue
        skill_paths.append("./" + relative_parent.as_posix())
    if "." in skill_paths:
        return (".",)
    return tuple(dict.fromkeys(skill_paths))


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
