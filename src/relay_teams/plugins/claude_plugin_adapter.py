# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import yaml

from relay_teams.plugins.path_resolution import resolve_plugin_component_path

_FRONT_MATTER_DELIMITER = "---"
_CLAUDE_ADAPTER_NAME = "claude"
_MAX_TIMEOUT_SECONDS = 600.0
_MILLISECONDS_PER_SECOND = 1000.0
_MANIFEST_KEYS = frozenset(
    {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "skills",
        "roles",
        "agents",
        "commands",
        "hooks",
        "mcp_servers",
        "mcpServers",
        "monitors",
        "settings",
        "user_config",
        "userConfig",
        "dependencies",
    }
)


def adapt_plugin_tree(*, plugin_root: Path, adapter: str) -> None:
    if adapter != _CLAUDE_ADAPTER_NAME:
        return
    manifest = _adapt_manifest(plugin_root)
    _adapt_hook_configs(plugin_root, manifest=manifest)
    adapt_agent_role_files(plugin_root=plugin_root)
    adapt_markdown_front_matter_files(plugin_root=plugin_root)


def adapt_agent_role_files(*, plugin_root: Path) -> None:
    agents_dir = plugin_root / "agents"
    if not agents_dir.exists() or not agents_dir.is_dir():
        return
    for agent_path in sorted(agents_dir.glob("*.md")):
        _adapt_agent_role_file(agent_path)


def adapt_markdown_front_matter_files(*, plugin_root: Path) -> None:
    for skill_path in sorted(plugin_root.rglob("SKILL.md")):
        _adapt_markdown_front_matter_file(skill_path)
    commands_dir = plugin_root / "commands"
    if not commands_dir.is_dir():
        return
    for command_path in sorted(commands_dir.glob("*.md")):
        _adapt_markdown_front_matter_file(command_path)


def _adapt_manifest(plugin_root: Path) -> dict[str, object]:
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        return {}
    parsed_raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    parsed = _string_key_mapping(parsed_raw)
    normalized = {key: value for key, value in parsed.items() if key in _MANIFEST_KEYS}
    _normalize_hook_timeouts(normalized.get("hooks"))
    manifest_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def _adapt_hook_configs(plugin_root: Path, *, manifest: dict[str, object]) -> None:
    for hook_path in _hook_config_paths(plugin_root=plugin_root, manifest=manifest):
        parsed_raw = json.loads(hook_path.read_text(encoding="utf-8-sig"))
        if _normalize_hook_timeouts(parsed_raw):
            hook_path.write_text(
                json.dumps(parsed_raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def _hook_config_paths(
    *, plugin_root: Path, manifest: dict[str, object]
) -> tuple[Path, ...]:
    manifest_hooks = manifest.get("hooks")
    if manifest_hooks is not None:
        return _manifest_hook_config_paths(
            plugin_root=plugin_root,
            manifest_hooks=manifest_hooks,
        )
    hooks_dir = plugin_root / "hooks"
    if not hooks_dir.exists() or not hooks_dir.is_dir():
        return ()
    return tuple(sorted(hooks_dir.glob("*.json")))


def _manifest_hook_config_paths(
    *, plugin_root: Path, manifest_hooks: object
) -> tuple[Path, ...]:
    if isinstance(manifest_hooks, str):
        return (
            _resolve_manifest_hook_path(
                plugin_root=plugin_root, raw_path=manifest_hooks
            ),
        )
    if isinstance(manifest_hooks, list | tuple):
        return tuple(
            _resolve_manifest_hook_path(plugin_root=plugin_root, raw_path=raw_path)
            for raw_path in manifest_hooks
            if isinstance(raw_path, str)
        )
    return ()


def _resolve_manifest_hook_path(*, plugin_root: Path, raw_path: str) -> Path:
    return resolve_plugin_component_path(plugin_root=plugin_root, raw_path=raw_path)


def _normalize_hook_timeouts(value: object) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key in {"timeout", "timeout_seconds"}:
                normalized = _normalize_timeout_value(item)
                if normalized is not item:
                    value[key] = normalized
                    changed = True
                continue
            if _normalize_hook_timeouts(item):
                changed = True
    elif isinstance(value, list):
        for item in value:
            if _normalize_hook_timeouts(item):
                changed = True
    return changed


def _normalize_timeout_value(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    if value <= _MILLISECONDS_PER_SECOND or value % _MILLISECONDS_PER_SECOND != 0:
        return value
    seconds = value / _MILLISECONDS_PER_SECOND
    if seconds <= _MAX_TIMEOUT_SECONDS:
        return seconds
    return value


def _adapt_agent_role_file(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    front_matter, body = _split_front_matter(raw)
    parsed_raw = yaml.safe_load(front_matter)
    parsed = _string_key_mapping(parsed_raw)
    role_id = _string_field(parsed, "role_id") or _string_field(parsed, "name")
    if not role_id:
        role_id = path.stem
    parsed["role_id"] = role_id
    parsed["name"] = _string_field(parsed, "name") or role_id
    parsed["description"] = _string_field(parsed, "description") or parsed["name"]
    parsed["version"] = _string_field(parsed, "version") or "1.0.0"
    parsed["mode"] = _string_field(parsed, "mode") or "subagent"
    parsed["tools"] = []
    if "mcp_servers" in parsed:
        parsed["mcp_servers"] = _string_list_field(parsed, "mcp_servers")
    if "skills" in parsed:
        parsed["skills"] = _string_list_field(parsed, "skills")
    rendered_front_matter = yaml.safe_dump(
        parsed,
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(
        f"{_FRONT_MATTER_DELIMITER}\n{rendered_front_matter}{_FRONT_MATTER_DELIMITER}\n{body}",
        encoding="utf-8",
    )


def _adapt_markdown_front_matter_file(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    front_matter, body = _split_front_matter(raw)
    if not front_matter:
        return
    try:
        yaml.safe_load(front_matter)
        return
    except yaml.YAMLError:
        pass
    parsed = _simple_front_matter_mapping(front_matter)
    if not parsed:
        return
    rendered_front_matter = yaml.safe_dump(
        parsed,
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(
        f"{_FRONT_MATTER_DELIMITER}\n{rendered_front_matter}{_FRONT_MATTER_DELIMITER}\n{body}",
        encoding="utf-8",
    )


def _split_front_matter(content: str) -> tuple[str, str]:
    if not content.startswith(_FRONT_MATTER_DELIMITER):
        return "", content
    lines = content.splitlines(keepends=True)
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FRONT_MATTER_DELIMITER:
            return "".join(lines[1:idx]), "".join(lines[idx + 1 :])
    return "", content


def _simple_front_matter_mapping(front_matter: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in front_matter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            return {}
        normalized_key = key.strip()
        if not normalized_key:
            return {}
        parsed[normalized_key] = value.strip()
    return parsed


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _string_field(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    return item.strip() if isinstance(item, str) else ""


def _string_list_field(value: dict[str, object], key: str) -> list[str]:
    item = value.get(key)
    if isinstance(item, list):
        return [str(entry).strip() for entry in item if str(entry).strip()]
    if isinstance(item, str):
        return [entry.strip() for entry in item.split(",") if entry.strip()]
    return []
