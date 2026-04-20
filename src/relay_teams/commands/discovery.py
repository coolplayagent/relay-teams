# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path
from threading import RLock

import yaml

from relay_teams.commands.command_models import (
    CommandEntry,
    CommandScope,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_app_config_dir, get_project_root_or_none
from relay_teams.trace import trace_span

logger = get_logger(__name__)

_COMMAND_FILE_PATTERN = re.compile(r"^(.+)\.md$", re.IGNORECASE)


def get_app_commands_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir) / "commands"


def get_project_commands_dir(project_root: Path | None = None) -> Path:
    effective_root = project_root or get_project_root_or_none() or Path.cwd()
    relay_teams_dir = effective_root / ".relay-teams" / "commands"
    codex_dir = effective_root / ".codex" / "commands"
    if codex_dir.exists():
        return codex_dir
    return relay_teams_dir


class CommandsDirectory:
    def __init__(
        self,
        *,
        app_commands_dir: Path,
        project_commands_dir: Path | None = None,
    ) -> None:
        self.app_commands_dir = app_commands_dir.expanduser().resolve()
        self.project_commands_dir = (
            project_commands_dir.expanduser().resolve()
            if project_commands_dir is not None
            else None
        )
        self._commands: dict[str, CommandEntry] = {}
        self._lock = RLock()

    @classmethod
    def from_default_scopes(
        cls,
        *,
        user_home_dir: Path | None = None,
        project_root: Path | None = None,
    ) -> CommandsDirectory:
        return cls(
            app_commands_dir=get_app_commands_dir(user_home_dir=user_home_dir),
            project_commands_dir=get_project_commands_dir(project_root=project_root),
        )

    def discover(self) -> None:
        with trace_span(
            logger,
            component="commands.discovery",
            operation="discover",
            attributes={
                "app_commands_dir": str(self.app_commands_dir),
                "project_commands_dir": (
                    str(self.project_commands_dir)
                    if self.project_commands_dir is not None
                    else None
                ),
            },
        ):
            discovered: dict[str, CommandEntry] = {}
            for scope, base_dir in self._iter_sources():
                if not base_dir.exists():
                    continue
                for path in sorted(base_dir.iterdir()):
                    if not path.is_file():
                        continue
                    match = _COMMAND_FILE_PATTERN.match(path.name)
                    if match is None:
                        continue
                    try:
                        command = self._load_command(path=path, scope=scope)
                        if command is not None:
                            discovered[command.name] = command
                    except Exception as exc:
                        logger.warning("Failed to load command at %s: %s", path, exc)
            with self._lock:
                self._commands = discovered

    def list_commands(self) -> list[CommandEntry]:
        with self._lock:
            return list(self._commands.values())

    def get_command(self, name: str) -> CommandEntry | None:
        with self._lock:
            return self._commands.get(name)

    def _iter_sources(self) -> tuple[tuple[CommandScope, Path], ...]:
        sources: list[tuple[CommandScope, Path]] = []
        sources.append((CommandScope.APP, self.app_commands_dir))
        if self.project_commands_dir is not None:
            sources.append((CommandScope.PROJECT, self.project_commands_dir))
        return tuple(sources)

    def _load_command(self, *, path: Path, scope: CommandScope) -> CommandEntry | None:
        with trace_span(
            logger,
            component="commands.discovery",
            operation="load_command",
            attributes={"path": str(path), "scope": scope.value},
        ):
            raw = path.read_text(encoding="utf-8")
            front_matter, body = _split_front_matter(raw)
            data = _as_object_mapping(yaml.safe_load(front_matter))
            if data is None:
                data = {}

            name = _coerce_string(data.get("name")) or path.stem
            description = _coerce_string(data.get("description"))
            argument_hint = _coerce_string(data.get("argument_hint"))
            allowed_modes_raw = data.get("allowed_modes")
            if isinstance(allowed_modes_raw, list):
                allowed_modes = [str(m) for m in allowed_modes_raw]
            else:
                allowed_modes = ["normal"]

            return CommandEntry(
                name=name,
                description=description,
                argument_hint=argument_hint,
                allowed_modes=allowed_modes,
                body=body.strip(),
                scope=scope,
                path=path,
            )


def _split_front_matter(content: str) -> tuple[str, str]:
    if not content.startswith("---"):
        return "", content

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", content

    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break

    if end_index is None:
        return "", content

    front_matter = "".join(lines[1:end_index])
    body = "".join(lines[end_index + 1 :])
    return front_matter, body


def _as_object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _coerce_string(value: object) -> str:
    return value if isinstance(value, str) else ""
