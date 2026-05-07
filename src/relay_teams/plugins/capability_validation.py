# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from json import loads
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, JsonValue
import yaml

from relay_teams.hooks.hook_models import HooksConfig
from relay_teams.hooks.hook_normalization import (
    normalize_hooks_payload,
    validate_hook_event_capabilities,
)
from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.plugins.mcp_sources import load_plugin_mcp_specs
from relay_teams.plugins.path_resolution import namespace_plugin_ref
from relay_teams.plugins.plugin_models import PluginComponentSource, PluginRecord
from relay_teams.plugins.substitution import substitute_plugin_vars
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry.defaults import build_default_registry


class _PluginRoleCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    mode: str = "primary"
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


_COMMAND_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*$"
)
_FRONT_MATTER_DELIMITER = "---"
_KNOWN_HOOK_EVENTS = frozenset(
    {
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "PermissionDenied",
        "PostToolUse",
        "PostToolUseFailure",
        "Stop",
        "StopFailure",
        "SubagentStart",
        "SubagentStop",
        "TaskCreated",
        "TaskCompleted",
        "PreCompact",
        "PostCompact",
        "Notification",
        "InstructionsLoaded",
    }
)
_COMMAND_ONLY_HOOK_EVENTS = frozenset({"SessionStart"})
_OBSERVE_ONLY_HOOK_EVENTS = frozenset(
    {
        "SessionEnd",
        "StopFailure",
        "SubagentStart",
        "InstructionsLoaded",
        "Notification",
        "PreCompact",
        "PostCompact",
    }
)
_HOOK_HANDLER_TYPES = frozenset({"command", "http", "prompt", "agent"})
_HOOK_ON_ERROR_VALUES = frozenset({"ignore", "fail"})
_HOOK_SHELL_VALUES = frozenset({"bash", "powershell"})
_HOOK_GROUP_KEYS = frozenset(
    {"name", "matcher", "role_ids", "session_modes", "run_kinds", "hooks"}
)
_HOOK_HANDLER_KEYS = frozenset(
    {
        "type",
        "name",
        "if",
        "if_rule",
        "timeout_seconds",
        "timeout",
        "run_async",
        "async",
        "on_error",
        "command",
        "shell",
        "url",
        "headers",
        "allowed_env_vars",
        "prompt",
        "model",
        "role_id",
        "async_rewake",
        "status_message",
    }
)


def validate_plugin_capabilities(
    *,
    record: PluginRecord,
    app_config_dir: Path,
    project_start_dir: Path | None,
    runtime_plugin_records: tuple[PluginRecord, ...] = (),
) -> None:
    plugin_sources = _merged_plugin_sources(
        current=runtime_plugin_records,
        candidate=record,
    )
    mcp_registry = McpConfigManager(app_config_dir=app_config_dir).load_registry(
        extra_specs=load_plugin_mcp_specs(
            tuple(source for item in plugin_sources for source in item.mcp_sources)
        )
    )
    skill_registry = SkillRegistry.from_config_dirs(
        app_config_dir=app_config_dir,
        project_start_dir=project_start_dir,
        plugin_sources=tuple(
            source for item in plugin_sources for source in item.skill_sources
        ),
    )
    role_capabilities = tuple(
        capability
        for item in plugin_sources
        for source in item.role_sources
        for capability in _load_plugin_role_capabilities(source)
    )
    tool_registry = build_default_registry()
    for role in role_capabilities:
        role_id = role.role_id
        if not role_id.startswith(f"{record.name}:"):
            continue
        tool_registry.validate_known(role.tools)
        mcp_registry.validate_known(role.mcp_servers)
        skill_registry.validate_known(role.skills)
    _validate_plugin_hooks(
        record=record,
        role_capabilities=role_capabilities,
    )
    _validate_plugin_commands(record=record)


def _merged_plugin_sources(
    *,
    current: tuple[PluginRecord, ...],
    candidate: PluginRecord,
) -> tuple[PluginRecord, ...]:
    return (
        *tuple(record for record in current if record.name != candidate.name),
        candidate,
    )


def _validate_plugin_hooks(
    *,
    record: PluginRecord,
    role_capabilities: tuple[_PluginRoleCapabilities, ...],
) -> None:
    known_role_ids = frozenset(role.role_id for role in role_capabilities)
    subagent_role_ids = frozenset(
        role.role_id for role in role_capabilities if role.mode in {"subagent", "all"}
    )
    for source in record.hook_sources:
        if source.inline_config is None and not source.path.exists():
            continue
        payload = _load_plugin_hook_payload(source)
        substituted = substitute_plugin_vars(
            value=payload,
            plugin_root=source.root_dir,
            plugin_data=source.data_dir,
            user_config=source.user_config,
            allow_env=True,
        )
        if not isinstance(substituted, dict):
            raise ValueError(f"Plugin hook config must be an object: {source.path}")
        try:
            normalized = normalize_hooks_payload(substituted)
        except ValueError as exc:
            raise ValueError(
                f"Invalid plugin hook config in {source.path}: {exc}"
            ) from exc
        if not isinstance(normalized, dict):
            raise ValueError(f"Plugin hook config must be an object: {source.path}")
        raw_hooks = normalized.get("hooks")
        if not isinstance(raw_hooks, dict):
            raise ValueError(f"Plugin hook config must contain hooks: {source.path}")
        for event_name, raw_groups in raw_hooks.items():
            if not isinstance(event_name, str) or event_name not in _KNOWN_HOOK_EVENTS:
                raise ValueError(f"Unknown plugin hook event: {event_name}")
            if not isinstance(raw_groups, list):
                raise ValueError(
                    f"Plugin hook event groups must be a list: {source.path}"
                )
            for raw_group in raw_groups:
                _validate_hook_group_agent_roles(
                    event_name=event_name,
                    raw_group=raw_group,
                    plugin_name=record.name,
                    known_role_ids=known_role_ids,
                    subagent_role_ids=subagent_role_ids,
                    source_path=source.path,
                )
                _validate_hook_group_runtime_capabilities(
                    event_name=event_name,
                    raw_group=raw_group,
                    source_path=source.path,
                )


def _load_plugin_hook_payload(source: PluginComponentSource) -> dict[str, JsonValue]:
    if source.inline_config is not None:
        return source.inline_config
    raw = loads(source.path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        return {str(key): _json_value(value) for key, value in raw.items()}
    return {}


def _validate_hook_group_agent_roles(
    *,
    event_name: str,
    raw_group: JsonValue,
    plugin_name: str,
    known_role_ids: frozenset[str],
    subagent_role_ids: frozenset[str],
    source_path: Path,
) -> None:
    if not isinstance(raw_group, dict):
        raise ValueError(f"Plugin hook group must be an object: {source_path}")
    _validate_object_keys(
        value=raw_group,
        allowed_keys=_HOOK_GROUP_KEYS,
        label="Plugin hook group",
        source_path=source_path,
    )
    raw_handlers = raw_group.get("hooks")
    if not isinstance(raw_handlers, list):
        raise ValueError(f"Plugin hook group must contain hooks: {source_path}")
    for raw_handler in raw_handlers:
        if not isinstance(raw_handler, dict):
            raise ValueError(f"Plugin hook handler must be an object: {source_path}")
        _validate_hook_handler(
            event_name=event_name,
            raw_handler=raw_handler,
            plugin_name=plugin_name,
            known_role_ids=known_role_ids,
            subagent_role_ids=subagent_role_ids,
            source_path=source_path,
        )


def _validate_hook_group_runtime_capabilities(
    *,
    event_name: str,
    raw_group: JsonValue,
    source_path: Path,
) -> None:
    try:
        config = HooksConfig.model_validate({"hooks": {event_name: [raw_group]}})
        validate_hook_event_capabilities(config=config)
    except ValueError as exc:
        raise ValueError(f"Invalid plugin hook group in {source_path}: {exc}") from exc


def _validate_hook_handler(
    *,
    event_name: str,
    raw_handler: Mapping[str, object],
    plugin_name: str,
    known_role_ids: frozenset[str],
    subagent_role_ids: frozenset[str],
    source_path: Path,
) -> None:
    _validate_object_keys(
        value=raw_handler,
        allowed_keys=_HOOK_HANDLER_KEYS,
        label="Plugin hook handler",
        source_path=source_path,
    )
    handler_type = _string_mapping_field(raw_handler, "type")
    if handler_type not in _HOOK_HANDLER_TYPES:
        raise ValueError(f"Unknown plugin hook handler type: {handler_type}")
    _validate_hook_handler_field_types(raw_handler=raw_handler, source_path=source_path)
    if _bool_mapping_field(raw_handler, "run_async") or _bool_mapping_field(
        raw_handler, "async"
    ):
        if handler_type != "command":
            raise ValueError("Only command hook handlers may run async")
    if event_name in _COMMAND_ONLY_HOOK_EVENTS and handler_type != "command":
        raise ValueError(f"{event_name} only supports command hook handlers")
    if event_name in _OBSERVE_ONLY_HOOK_EVENTS and handler_type not in {
        "command",
        "http",
    }:
        raise ValueError(f"{event_name} only supports command or http hook handlers")
    if handler_type == "command":
        if not _string_mapping_field(raw_handler, "command"):
            raise ValueError("command hook requires command")
        return
    if handler_type == "http":
        if not _string_mapping_field(raw_handler, "url"):
            raise ValueError("http hook requires url")
        return
    if handler_type == "prompt":
        if not _string_mapping_field(raw_handler, "prompt"):
            raise ValueError("prompt hook requires prompt")
        return
    if not _string_mapping_field(raw_handler, "prompt"):
        raise ValueError("agent hook requires prompt")
    role_id = _string_mapping_field(raw_handler, "role_id")
    if not role_id:
        raise ValueError("Agent hook role_id is required.")
    namespaced_role_id = (
        role_id
        if ":" in role_id
        else namespace_plugin_ref(
            plugin_name=plugin_name,
            local_name=role_id,
        )
    )
    if namespaced_role_id in subagent_role_ids:
        return
    if namespaced_role_id in known_role_ids:
        raise ValueError(
            f"Agent hook role_id must reference a subagent role: {namespaced_role_id}"
        )
    raise ValueError(f"Unknown agent hook role_id: {namespaced_role_id}")


def _validate_hook_handler_field_types(
    *,
    raw_handler: Mapping[str, object],
    source_path: Path,
) -> None:
    for key in (
        "type",
        "name",
        "if",
        "if_rule",
        "command",
        "url",
        "prompt",
        "model",
        "role_id",
        "status_message",
    ):
        _validate_optional_string_field(
            value=raw_handler,
            key=key,
            label="Plugin hook handler",
            source_path=source_path,
        )
    for key in ("run_async", "async", "async_rewake"):
        _validate_optional_bool_field(
            value=raw_handler,
            key=key,
            label="Plugin hook handler",
            source_path=source_path,
        )
    for key in ("timeout_seconds", "timeout"):
        _validate_optional_timeout_field(
            value=raw_handler,
            key=key,
            source_path=source_path,
        )
    on_error = _string_mapping_field(raw_handler, "on_error")
    if on_error and on_error not in _HOOK_ON_ERROR_VALUES:
        raise ValueError(f"Invalid plugin hook on_error in {source_path}: {on_error}")
    shell = _string_mapping_field(raw_handler, "shell")
    if shell and shell not in _HOOK_SHELL_VALUES:
        raise ValueError(f"Invalid plugin hook shell in {source_path}: {shell}")
    _validate_optional_string_mapping_field(
        value=raw_handler,
        key="headers",
        label="Plugin hook handler",
        source_path=source_path,
    )
    _validate_optional_string_sequence_field(
        value=raw_handler,
        key="allowed_env_vars",
        label="Plugin hook handler",
        source_path=source_path,
    )


def _validate_optional_string_field(
    *,
    value: Mapping[str, object],
    key: str,
    label: str,
    source_path: Path,
) -> None:
    if key not in value:
        return
    raw_value = value[key]
    if raw_value is not None and not isinstance(raw_value, str):
        raise ValueError(f"{label} {key} must be a string: {source_path}")


def _validate_optional_bool_field(
    *,
    value: Mapping[str, object],
    key: str,
    label: str,
    source_path: Path,
) -> None:
    if key not in value:
        return
    if not isinstance(value[key], bool):
        raise ValueError(f"{label} {key} must be a boolean: {source_path}")


def _validate_optional_timeout_field(
    *,
    value: Mapping[str, object],
    key: str,
    source_path: Path,
) -> None:
    if key not in value:
        return
    raw_timeout = value[key]
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int | float):
        raise ValueError(f"Plugin hook handler {key} must be a number: {source_path}")
    if raw_timeout <= 0.0 or raw_timeout > 600.0:
        raise ValueError(
            f"Plugin hook handler {key} must be greater than 0 and at most 600: "
            f"{source_path}"
        )


def _validate_optional_string_mapping_field(
    *,
    value: Mapping[str, object],
    key: str,
    label: str,
    source_path: Path,
) -> None:
    if key not in value:
        return
    raw_mapping = value[key]
    if not isinstance(raw_mapping, dict):
        raise ValueError(f"{label} {key} must be an object: {source_path}")
    invalid_keys = tuple(
        item_key
        for item_key, item_value in raw_mapping.items()
        if not isinstance(item_key, str) or not isinstance(item_value, str)
    )
    if invalid_keys:
        raise ValueError(f"{label} {key} must contain only strings: {source_path}")


def _validate_optional_string_sequence_field(
    *,
    value: Mapping[str, object],
    key: str,
    label: str,
    source_path: Path,
) -> None:
    if key not in value:
        return
    _string_sequence(
        value=value[key],
        field_name=f"{label} {key}",
        source_path=source_path,
    )


def _validate_object_keys(
    *,
    value: Mapping[str, object],
    allowed_keys: frozenset[str],
    label: str,
    source_path: Path,
) -> None:
    unknown_keys = sorted(key for key in value if key not in allowed_keys)
    if unknown_keys:
        raise ValueError(
            f"{label} contains unknown field(s) in {source_path}: "
            f"{', '.join(unknown_keys)}"
        )


def _validate_plugin_commands(*, record: PluginRecord) -> None:
    names: set[str] = set()
    aliases: set[str] = set()
    for source in record.command_sources:
        if not source.path.exists() or not source.path.is_dir():
            continue
        for command_path in sorted(source.path.rglob("*.md")):
            rel = command_path.relative_to(source.path)
            command_name, command_aliases = _validate_plugin_command_file(
                path=command_path,
                rel=rel,
                plugin_name=source.plugin_name,
            )
            if command_name in names:
                raise ValueError(f"Duplicate plugin command name: {command_name}")
            if command_name in aliases:
                raise ValueError(
                    f"Plugin command name conflicts with an alias: {command_name}"
                )
            names.add(command_name)
            for alias in command_aliases:
                if alias in aliases:
                    raise ValueError(f"Duplicate plugin command alias: {alias}")
                if alias in names:
                    raise ValueError(
                        f"Plugin command alias conflicts with a name: {alias}"
                    )
                aliases.add(alias)


def _validate_plugin_command_file(
    *,
    path: Path,
    rel: Path,
    plugin_name: str,
) -> tuple[str, tuple[str, ...]]:
    raw = path.read_text(encoding="utf-8")
    front_matter, body = _split_optional_front_matter(raw)
    parsed = yaml.safe_load(front_matter) if front_matter else {}
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Plugin command front matter must be an object: {path}")
    data = _string_key_mapping(parsed)
    default_name = namespace_plugin_ref(
        plugin_name=plugin_name,
        local_name=rel.with_suffix("").as_posix(),
    )
    raw_name = _string_mapping_field(data, "name")
    if raw_name and not _is_valid_command_name(raw_name):
        raise ValueError(f"Invalid plugin command name in {path}: {raw_name}")
    command_name = (
        namespace_plugin_ref(plugin_name=plugin_name, local_name=raw_name)
        if raw_name
        else default_name
    )
    if not _is_valid_command_name(command_name):
        raise ValueError(f"Invalid plugin command name in {path}: {command_name}")
    aliases = _plugin_command_aliases(
        plugin_name=plugin_name,
        value=data.get("aliases"),
        source_path=path,
    )
    _validate_allowed_modes(value=data.get("allowed_modes"), source_path=path)
    _validate_allowed_modes(value=data.get("allowed-modes"), source_path=path)
    if not body.strip():
        raise ValueError(f"Plugin command template must not be empty: {path}")
    return command_name, aliases


def _split_optional_front_matter(content: str) -> tuple[str, str]:
    if not content.startswith(_FRONT_MATTER_DELIMITER):
        return "", content

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
        return "", content

    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FRONT_MATTER_DELIMITER:
            return "".join(lines[1:idx]), "".join(lines[idx + 1 :])
    raise ValueError("Invalid plugin command YAML front matter delimiters")


def _plugin_command_aliases(
    *,
    plugin_name: str,
    value: object,
    source_path: Path,
) -> tuple[str, ...]:
    if value is None:
        return ()
    aliases: list[str] = []
    for alias in _string_sequence(
        value=value,
        field_name="Plugin command aliases",
        source_path=source_path,
    ):
        normalized = alias.removeprefix("/").strip()
        if not _is_valid_command_name(normalized):
            raise ValueError(f"Invalid plugin command alias in {source_path}: {alias}")
        namespaced = namespace_plugin_ref(
            plugin_name=plugin_name,
            local_name=normalized,
        )
        if namespaced not in aliases:
            aliases.append(namespaced)
    return tuple(aliases)


def _validate_allowed_modes(*, value: object, source_path: Path) -> None:
    if value is None:
        return
    _string_sequence(
        value=value,
        field_name="Plugin command allowed_modes",
        source_path=source_path,
    )


def _string_sequence(
    *,
    value: object,
    field_name: str,
    source_path: Path,
) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"{field_name} must contain only strings: {source_path}"
                )
            if item.strip():
                items.append(item.strip())
        return tuple(items)
    if isinstance(value, tuple):
        items = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"{field_name} must contain only strings: {source_path}"
                )
            if item.strip():
                items.append(item.strip())
        return tuple(items)
    raise ValueError(f"{field_name} must be a string or list: {source_path}")


def _string_key_mapping(value: dict[object, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            result[key] = item
    return result


def _string_mapping_field(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    return item.strip() if isinstance(item, str) else ""


def _bool_mapping_field(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    return item if isinstance(item, bool) else False


def _is_valid_command_name(name: str) -> bool:
    return bool(_COMMAND_NAME_RE.match(name.strip()))


def _load_plugin_role_capabilities(
    source: PluginComponentSource,
) -> tuple[_PluginRoleCapabilities, ...]:
    if not source.path.exists() or not source.path.is_dir():
        return ()
    capabilities: list[_PluginRoleCapabilities] = []
    for role_path in sorted(source.path.glob("*.md")):
        capabilities.append(
            _load_plugin_role_capability(path=role_path, plugin_name=source.plugin_name)
        )
    return tuple(capabilities)


def _load_plugin_role_capability(
    *,
    path: Path,
    plugin_name: str,
) -> _PluginRoleCapabilities:
    front_matter = _split_role_front_matter(path.read_text(encoding="utf-8"))
    parsed = yaml.safe_load(front_matter)
    if not isinstance(parsed, dict):
        raise ValueError(f"Invalid front matter for plugin role file: {path}")
    role_id = str(parsed.get("role_id") or "").strip()
    if not role_id:
        raise ValueError(f"Missing role_id in plugin role file: {path}")
    return _PluginRoleCapabilities(
        role_id=namespace_plugin_ref(plugin_name=plugin_name, local_name=role_id),
        mode=str(parsed.get("mode") or "primary").strip(),
        tools=_string_tuple(parsed.get("tools")),
        mcp_servers=_namespace_refs(
            plugin_name=plugin_name,
            refs=_string_tuple(parsed.get("mcp_servers")),
        ),
        skills=_namespace_refs(
            plugin_name=plugin_name,
            refs=_string_tuple(parsed.get("skills")),
        ),
    )


def _split_role_front_matter(content: str) -> str:
    content = content.lstrip("\ufeff")
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("Plugin role markdown must start with YAML front matter")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "".join(lines[1:idx])
    raise ValueError("Invalid plugin role YAML front matter delimiters")


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("Plugin role capability references must be lists")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _namespace_refs(*, plugin_name: str, refs: tuple[str, ...]) -> tuple[str, ...]:
    namespaced: list[str] = []
    for ref in refs:
        if ref == "*" or ":" in ref:
            namespaced.append(ref)
            continue
        namespaced.append(namespace_plugin_ref(plugin_name=plugin_name, local_name=ref))
    return tuple(namespaced)


def _json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
