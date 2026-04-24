# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CommandScope(str, Enum):
    APP = "app"
    PROJECT = "project"


class CommandDiscoverySource(str, Enum):
    APP = "app"
    PROJECT_CODEX = "project_codex"
    PROJECT_CLAUDE = "project_claude"
    PROJECT_OPENCODE = "project_opencode"
    PROJECT_RELAY_TEAMS = "project_relay_teams"


class CommandCreateScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"


class CommandCreateSource(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    RELAY_TEAMS = "relay_teams"


class CommandDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    argument_hint: str = ""
    allowed_modes: tuple[str, ...] = ("normal",)
    template: str
    scope: CommandScope
    discovery_source: CommandDiscoverySource
    source_path: Path


class CommandSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    argument_hint: str = ""
    allowed_modes: tuple[str, ...] = ("normal",)
    scope: CommandScope
    discovery_source: CommandDiscoverySource
    source_path: Path


class CommandDetail(CommandSummary):
    template: str


class CommandCatalogWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    root_path: Optional[Path] = None
    can_create_commands: bool = False
    commands: tuple[CommandDetail, ...] = ()


class CommandCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_commands: tuple[CommandDetail, ...] = ()
    workspaces: tuple[CommandCatalogWorkspace, ...] = ()


class CommandCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: CommandCreateScope
    workspace_id: Optional[str] = Field(default=None, min_length=1)
    source: Optional[CommandCreateSource] = None
    relative_path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    description: str = ""
    argument_hint: str = ""
    allowed_modes: tuple[str, ...] = ("normal",)
    template: str = Field(min_length=1)


class CommandCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: CommandDetail
    workspace_id: Optional[str] = None


class CommandUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: Path
    name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    description: str = ""
    argument_hint: str = ""
    allowed_modes: tuple[str, ...] = ("normal",)
    template: str = Field(min_length=1)


class CommandUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: CommandDetail
    workspace_id: Optional[str] = None


class CommandResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    raw_text: str
    mode: str = "normal"
    cwd: Optional[str] = None


class CommandResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched: bool
    raw_text: str
    parsed_name: Optional[str] = None
    resolved_name: Optional[str] = None
    args: str = ""
    command: Optional[CommandSummary] = None
    expanded_prompt: Optional[str] = None
    expanded_prompt_length: int = 0


def command_summary_from_definition(
    command: CommandDefinition,
) -> CommandSummary:
    return CommandSummary(
        name=command.name,
        aliases=command.aliases,
        description=command.description,
        argument_hint=command.argument_hint,
        allowed_modes=command.allowed_modes,
        scope=command.scope,
        discovery_source=command.discovery_source,
        source_path=command.source_path,
    )


def command_detail_from_definition(
    command: CommandDefinition,
) -> CommandDetail:
    return CommandDetail(
        name=command.name,
        aliases=command.aliases,
        description=command.description,
        argument_hint=command.argument_hint,
        allowed_modes=command.allowed_modes,
        scope=command.scope,
        discovery_source=command.discovery_source,
        source_path=command.source_path,
        template=command.template,
    )
