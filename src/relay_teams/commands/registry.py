# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict

from relay_teams.commands.command_models import (
    CommandDefinition,
    CommandScope,
    CommandResolveResponse,
    command_summary_from_definition,
)
from relay_teams.commands.discovery import discover_commands
from relay_teams.logger import get_logger
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)
_TEMPLATE_VARIABLES = {
    "args": re.compile(r"\{\{\s*args\s*\}\}"),
    "workspace_root": re.compile(r"\{\{\s*workspace_root\s*\}\}"),
    "cwd": re.compile(r"\{\{\s*cwd\s*\}\}"),
}
_ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"


class CommandModeNotAllowed(ValueError):
    def __init__(self, *, command_name: str, mode: str) -> None:
        super().__init__(f"Command '{command_name}' is not allowed in mode '{mode}'")
        self.command_name = command_name
        self.mode = mode


class CommandRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_config_dir: Path

    def list_commands(
        self,
        *,
        workspace_root: Optional[Path],
    ) -> tuple[CommandDefinition, ...]:
        with trace_span(
            LOGGER,
            component="commands.registry",
            operation="list_commands",
            attributes={
                "workspace_root": str(workspace_root) if workspace_root else ""
            },
        ):
            command_map, _ = self._build_maps(workspace_root=workspace_root)
        return tuple(sorted(command_map.values(), key=_command_sort_key))

    def list_app_commands(self) -> tuple[CommandDefinition, ...]:
        commands = _effective_commands(
            discover_commands(
                app_config_dir=self.app_config_dir,
                workspace_root=None,
            )
        )
        return tuple(
            sorted(
                commands,
                key=_command_sort_key,
            )
        )

    def list_project_commands(
        self,
        *,
        workspace_root: Optional[Path],
    ) -> tuple[CommandDefinition, ...]:
        if workspace_root is None:
            return ()
        commands = _effective_commands(
            tuple(
                command
                for command in discover_commands(
                    app_config_dir=self.app_config_dir,
                    workspace_root=workspace_root,
                )
                if command.scope == CommandScope.PROJECT
            )
        )
        return tuple(
            sorted(
                commands,
                key=_command_sort_key,
            )
        )

    def get_discovered_command_by_source_path(
        self,
        *,
        source_path: Path,
        workspace_root: Optional[Path],
    ) -> Optional[CommandDefinition]:
        target_path = source_path.resolve()
        for command in discover_commands(
            app_config_dir=self.app_config_dir,
            workspace_root=workspace_root,
        ):
            if command.source_path.resolve() == target_path:
                return command
        return None

    def get_command(
        self,
        name: str,
        *,
        workspace_root: Optional[Path],
    ) -> Optional[CommandDefinition]:
        with trace_span(
            LOGGER,
            component="commands.registry",
            operation="get_command",
            attributes={"name": name},
        ):
            command_map, alias_map = self._build_maps(workspace_root=workspace_root)
            safe_name = name.strip()
        return command_map.get(safe_name) or alias_map.get(safe_name)

    def resolve(
        self,
        *,
        raw_text: str,
        mode: str,
        workspace_root: Optional[Path],
        cwd: Optional[Path],
    ) -> CommandResolveResponse:
        parsed_name, args = _parse_command_text(raw_text)
        if parsed_name is None:
            return CommandResolveResponse(
                matched=False,
                raw_text=raw_text,
                args=args,
            )

        command = self.get_command(parsed_name, workspace_root=workspace_root)
        if command is None:
            return CommandResolveResponse(
                matched=False,
                raw_text=raw_text,
                parsed_name=parsed_name,
                args=args,
            )
        normalized_mode = mode.strip() or "normal"
        if normalized_mode not in command.allowed_modes:
            raise CommandModeNotAllowed(command_name=command.name, mode=normalized_mode)

        expanded_prompt = _expand_template(
            command.template,
            args=args,
            workspace_root=workspace_root,
            cwd=cwd or workspace_root,
        )
        return CommandResolveResponse(
            matched=True,
            raw_text=raw_text,
            parsed_name=parsed_name,
            resolved_name=command.name,
            args=args,
            command=command_summary_from_definition(command),
            expanded_prompt=expanded_prompt,
            expanded_prompt_length=len(expanded_prompt),
        )

    def _build_maps(
        self,
        *,
        workspace_root: Optional[Path],
    ) -> tuple[dict[str, CommandDefinition], dict[str, CommandDefinition]]:
        alias_map: dict[str, CommandDefinition] = {}
        effective_commands = _effective_commands(
            discover_commands(
                app_config_dir=self.app_config_dir,
                workspace_root=workspace_root,
            )
        )
        command_map = {command.name: command for command in effective_commands}

        for command in effective_commands:
            for alias in command.aliases:
                if alias in command_map:
                    continue
                existing_alias = alias_map.get(alias)
                if existing_alias is not None:
                    LOGGER.warning(
                        "Overriding duplicate command alias %s from %s with %s",
                        alias,
                        existing_alias.source_path,
                        command.source_path,
                    )
                alias_map[alias] = command
        return command_map, alias_map


def _effective_commands(
    commands: tuple[CommandDefinition, ...],
) -> tuple[CommandDefinition, ...]:
    command_map: dict[str, CommandDefinition] = {}
    effective_commands: list[CommandDefinition] = []
    for command in commands:
        existing = command_map.get(command.name)
        if existing is not None:
            LOGGER.warning(
                "Overriding duplicate command %s from %s (%s) with %s (%s)",
                command.name,
                existing.source_path,
                existing.scope.value,
                command.source_path,
                command.scope.value,
            )
            effective_commands = [
                candidate
                for candidate in effective_commands
                if candidate.name != command.name
            ]
        command_map[command.name] = command
        effective_commands.append(command)
    return tuple(effective_commands)


def _parse_command_text(raw_text: str) -> tuple[Optional[str], str]:
    source = str(raw_text or "")
    if not source.startswith("/"):
        return None, ""
    body = source[1:]
    if not body:
        return None, ""
    match = re.match(r"(?P<name>\S+)(?P<args>[\s\S]*)", body)
    if match is None:
        return None, ""
    name = match.group("name").strip()
    args = match.group("args").lstrip()
    return (name or None), args


def _expand_template(
    template: str,
    *,
    args: str,
    workspace_root: Optional[Path],
    cwd: Optional[Path],
) -> str:
    workspace_root_text = str(workspace_root.resolve()) if workspace_root else ""
    cwd_text = str(cwd.resolve()) if cwd else workspace_root_text
    has_args_placeholder = (
        bool(_TEMPLATE_VARIABLES["args"].search(template))
        or _ARGUMENTS_PLACEHOLDER in template
    )
    expanded = _TEMPLATE_VARIABLES["args"].sub(lambda _: args, template)
    expanded = _TEMPLATE_VARIABLES["workspace_root"].sub(
        lambda _: workspace_root_text,
        expanded,
    )
    expanded = _TEMPLATE_VARIABLES["cwd"].sub(lambda _: cwd_text, expanded)
    expanded = expanded.replace(_ARGUMENTS_PLACEHOLDER, args)
    expanded = expanded.strip()
    if args.strip() and not has_args_placeholder:
        expanded = f"{expanded}\n\n{args.strip()}" if expanded else args.strip()
    return expanded


def _command_sort_key(command: CommandDefinition) -> tuple[str, str]:
    return command.name.casefold(), command.name
