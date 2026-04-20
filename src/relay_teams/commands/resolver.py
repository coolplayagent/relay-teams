# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

from relay_teams.commands.command_models import ResolveResult
from relay_teams.commands.registry import CommandRegistry
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

_TEMPLATE_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


class CommandResolver:
    def __init__(
        self,
        *,
        registry: CommandRegistry,
        workspace_root: Path | None = None,
    ) -> None:
        self._registry = registry
        self._workspace_root = workspace_root or Path.cwd()

    def try_resolve(
        self,
        raw_text: str,
        *,
        mode: str = "normal",
    ) -> ResolveResult | None:
        if not raw_text.startswith("/"):
            return None

        parts = raw_text[1:].split(maxsplit=1)
        command_name = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        command = self._registry.get_command(command_name)
        if command is None:
            return None

        if mode not in command.allowed_modes:
            raise ValueError(
                f"Command '{command_name}' is not allowed in mode '{mode}'"
            )

        expanded = _expand_template(
            command.body,
            args=args,
            workspace_root=self._workspace_root,
        )

        return ResolveResult(
            command_name=command.name,
            scope=command.scope,
            raw_text=raw_text,
            expanded_prompt=expanded,
            args=args,
            prompt_length=len(expanded),
        )

    def resolve(
        self,
        raw_text: str,
        *,
        mode: str = "normal",
    ) -> ResolveResult:
        result = self.try_resolve(raw_text, mode=mode)
        if result is None:
            raise KeyError(f"No command found for: {raw_text}")
        return result


def _expand_template(
    template: str,
    *,
    args: str,
    workspace_root: Path,
) -> str:
    replacements: dict[str, str] = {
        "args": args,
        "workspace_root": str(workspace_root),
        "cwd": str(Path.cwd()),
    }

    def _replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return replacements.get(var_name, match.group(0))

    return _TEMPLATE_VAR_PATTERN.sub(_replacer, template)
