# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Optional

import yaml

from relay_teams.commands.command_models import (
    CommandDefinition,
    CommandDiscoverySource,
    CommandScope,
)
from relay_teams.logger import get_logger
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)
_COMMAND_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*$"
)
_FRONT_MATTER_DELIMITER = "---"
DEFAULT_COMMAND_MAX_DEPTH = 8


class CommandDiscoveryLocation:
    __slots__ = ("base_dir", "scope", "source")

    def __init__(
        self,
        *,
        base_dir: Path,
        scope: CommandScope,
        source: CommandDiscoverySource,
    ) -> None:
        self.base_dir = base_dir
        self.scope = scope
        self.source = source


def discover_commands(
    *,
    app_config_dir: Path,
    workspace_root: Optional[Path],
    max_depth: int = DEFAULT_COMMAND_MAX_DEPTH,
) -> tuple[CommandDefinition, ...]:
    locations = _build_locations(
        app_config_dir=app_config_dir,
        workspace_root=workspace_root,
    )
    commands: list[CommandDefinition] = []
    with trace_span(
        LOGGER,
        component="commands.discovery",
        operation="discover",
        attributes={
            "locations": [
                {
                    "scope": location.scope.value,
                    "source": location.source.value,
                    "base_dir": str(location.base_dir),
                }
                for location in locations
            ],
            "max_depth": max_depth,
        },
    ):
        for location in locations:
            commands.extend(_discover_location(location, max_depth=max_depth))
    return tuple(commands)


def _build_locations(
    *,
    app_config_dir: Path,
    workspace_root: Optional[Path],
) -> tuple[CommandDiscoveryLocation, ...]:
    locations: list[CommandDiscoveryLocation] = [
        CommandDiscoveryLocation(
            base_dir=(app_config_dir / "commands").resolve(),
            scope=CommandScope.APP,
            source=CommandDiscoverySource.APP,
        )
    ]
    if workspace_root is None:
        return tuple(locations)

    root = workspace_root.resolve()
    locations.extend(
        [
            CommandDiscoveryLocation(
                base_dir=(root / ".codex" / "commands").resolve(),
                scope=CommandScope.PROJECT,
                source=CommandDiscoverySource.PROJECT_CODEX,
            ),
            CommandDiscoveryLocation(
                base_dir=(root / ".claude" / "commands").resolve(),
                scope=CommandScope.PROJECT,
                source=CommandDiscoverySource.PROJECT_CLAUDE,
            ),
            CommandDiscoveryLocation(
                base_dir=(root / ".opencode" / "command").resolve(),
                scope=CommandScope.PROJECT,
                source=CommandDiscoverySource.PROJECT_OPENCODE,
            ),
            CommandDiscoveryLocation(
                base_dir=(root / ".opencode" / "commands").resolve(),
                scope=CommandScope.PROJECT,
                source=CommandDiscoverySource.PROJECT_OPENCODE,
            ),
            CommandDiscoveryLocation(
                base_dir=(root / ".relay-teams" / "commands").resolve(),
                scope=CommandScope.PROJECT,
                source=CommandDiscoverySource.PROJECT_RELAY_TEAMS,
            ),
        ]
    )
    return tuple(locations)


def _discover_location(
    location: CommandDiscoveryLocation,
    *,
    max_depth: int,
) -> tuple[CommandDefinition, ...]:
    if not location.base_dir.exists() or not location.base_dir.is_dir():
        return ()

    commands: list[CommandDefinition] = []
    for path in sorted(location.base_dir.rglob("*.md")):
        try:
            rel = path.relative_to(location.base_dir)
            if len(rel.parts) > max_depth + 1:
                continue
            command = _load_command(path=path, rel=rel, location=location)
            if command is not None:
                commands.append(command)
        except Exception as exc:
            LOGGER.warning("Failed to load command at %s: %s", path, exc)
    return tuple(commands)


def _load_command(
    *,
    path: Path,
    rel: Path,
    location: CommandDiscoveryLocation,
) -> Optional[CommandDefinition]:
    raw = path.read_text(encoding="utf-8")
    try:
        front_matter, body = _split_optional_front_matter(raw)
        data = _as_object_mapping(yaml.safe_load(front_matter)) if front_matter else {}
    except Exception as exc:
        LOGGER.warning("Skipping command %s due to parsing error: %s", path, exc)
        return None

    default_name = _default_command_name(rel=rel, source=location.source)
    raw_name = data.get("name")
    provided_name = raw_name.strip() if isinstance(raw_name, str) else ""
    name = provided_name if is_valid_command_name(provided_name) else default_name
    if provided_name and name != provided_name:
        LOGGER.warning(
            "Ignoring invalid command front matter name %s in %s",
            provided_name,
            path,
        )
    if not is_valid_command_name(name):
        LOGGER.warning("Skipping command %s with invalid name %s", path, name)
        return None

    aliases = _aliases(
        value=data.get("aliases"),
        default_aliases=_default_aliases(name=name, rel=rel, source=location.source),
    )
    description = _string_field(data, "description")
    argument_hint = _string_field(data, "argument_hint") or _string_field(
        data, "argument-hint"
    )
    allowed_modes = _allowed_modes(data.get("allowed_modes"))
    return CommandDefinition(
        name=name,
        aliases=aliases,
        description=description,
        argument_hint=argument_hint,
        allowed_modes=allowed_modes,
        template=body.strip(),
        scope=location.scope,
        discovery_source=location.source,
        source_path=path.resolve(),
    )


def _split_optional_front_matter(content: str) -> tuple[str, str]:
    if not content.startswith(_FRONT_MATTER_DELIMITER):
        return "", content

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
        return "", content

    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FRONT_MATTER_DELIMITER:
            end_index = idx
            break
    if end_index is None:
        raise ValueError("Invalid YAML front matter delimiters")
    return "".join(lines[1:end_index]), "".join(lines[end_index + 1 :])


def _default_command_name(
    *,
    rel: Path,
    source: CommandDiscoverySource,
) -> str:
    rel_name = rel.with_suffix("").as_posix()
    if source == CommandDiscoverySource.PROJECT_CLAUDE:
        parts = rel.with_suffix("").parts
        if len(parts) >= 2 and parts[0] == "opsx":
            return f"opsx:{'/'.join(parts[1:])}"
    return rel_name


def _default_aliases(
    *,
    name: str,
    rel: Path,
    source: CommandDiscoverySource,
) -> tuple[str, ...]:
    aliases: list[str] = []
    rel_name = rel.with_suffix("").as_posix()
    if source == CommandDiscoverySource.PROJECT_OPENCODE and rel_name.startswith(
        "opsx-"
    ):
        alias = f"opsx:{rel_name.removeprefix('opsx-')}"
        if alias != name and is_valid_command_name(alias):
            aliases.append(alias)
    return tuple(aliases)


def _allowed_modes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ("normal",)
    if isinstance(value, list):
        modes = tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
        return modes or ("normal",)
    if isinstance(value, tuple):
        modes = tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
        return modes or ("normal",)
    return ("normal",)


def _aliases(
    *,
    value: object,
    default_aliases: tuple[str, ...],
) -> tuple[str, ...]:
    aliases: list[str] = []
    for item in _string_sequence(value):
        alias = item.removeprefix("/").strip()
        if is_valid_command_name(alias) and alias not in aliases:
            aliases.append(alias)
    for alias in default_aliases:
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
    if isinstance(value, tuple):
        return tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
    return ()


def _string_field(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _as_object_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            result[key] = item
    return result


def is_valid_command_name(name: str) -> bool:
    return bool(_COMMAND_NAME_RE.match(name.strip()))
