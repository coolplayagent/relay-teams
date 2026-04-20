# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from relay_teams.commands.command_models import (
    CommandEntry,
    CommandSummary,
)
from relay_teams.commands.discovery import CommandsDirectory
from relay_teams.logger import get_logger
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)


class CommandRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    directory: CommandsDirectory

    @classmethod
    def from_default_scopes(
        cls,
        *,
        user_home_dir: Path | None = None,
        project_root: Path | None = None,
    ) -> CommandRegistry:
        return cls(
            directory=CommandsDirectory.from_default_scopes(
                user_home_dir=user_home_dir,
                project_root=project_root,
            )
        )

    def list_commands(self) -> tuple[CommandEntry, ...]:
        with trace_span(
            LOGGER,
            component="commands.registry",
            operation="list_commands",
        ):
            self.directory.discover()
            return tuple(self.directory.list_commands())

    def get_command(self, name: str) -> CommandEntry | None:
        with trace_span(
            LOGGER,
            component="commands.registry",
            operation="get_command",
            attributes={"command_name": name},
        ):
            self.directory.discover()
            return self.directory.get_command(name)

    def list_summaries(self) -> tuple[CommandSummary, ...]:
        return tuple(
            CommandSummary(
                name=cmd.name,
                description=cmd.description,
                scope=cmd.scope,
                argument_hint=cmd.argument_hint,
            )
            for cmd in self.list_commands()
        )

    def resolve(self, name: str) -> CommandEntry | None:
        self.directory.discover()
        return self.directory.get_command(name)
