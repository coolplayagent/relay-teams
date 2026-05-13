# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path
import re

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)

from relay_teams.validation import RequiredIdentifierStr

_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class PluginScope(str, Enum):
    LOCAL = "local"
    USER = "user"
    PROJECT = "project"
    PROJECT_LOCAL = "project_local"
    MANAGED = "managed"


class PluginInstallSourceKind(str, Enum):
    LOCAL = "local"
    GIT = "git"
    GIT_SUBDIR = "git_subdir"
    HTTP_ARCHIVE = "http_archive"
    MARKETPLACE = "marketplace"
    UNSUPPORTED = "unsupported"


class PluginDiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PluginComponentKind(str, Enum):
    SKILLS = "skills"
    ROLES = "roles"
    COMMANDS = "commands"
    HOOKS = "hooks"
    MCP_SERVERS = "mcp_servers"
    MONITORS = "monitors"
    SETTINGS = "settings"


class PluginAuthor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    email: str = ""
    url: str = ""


class PluginDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RequiredIdentifierStr
    version: str | None = None


class PluginUserConfigField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="string")
    title: str = ""
    description: str = ""
    default: JsonValue | None = None
    sensitive: bool = False
    required: bool = False


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_url: str | None = Field(
        default=None,
        validation_alias="$schema",
        exclude=True,
    )
    name: RequiredIdentifierStr
    version: str | None = None
    description: str = ""
    author: PluginAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: tuple[str, ...] = ()
    skills: str | tuple[str, ...] | None = None
    roles: str | tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("roles", "agents"),
    )
    commands: str | tuple[str, ...] | None = None
    hooks: str | tuple[str, ...] | dict[str, JsonValue] | None = None
    mcp_servers: str | tuple[str, ...] | dict[str, JsonValue] | None = Field(
        default=None,
        validation_alias=AliasChoices("mcp_servers", "mcpServers"),
    )
    monitors: str | tuple[str, ...] | dict[str, JsonValue] | None = None
    settings: str | dict[str, JsonValue] | None = None
    user_config: dict[str, PluginUserConfigField] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("user_config", "userConfig"),
    )
    dependencies: tuple[PluginDependency, ...] = ()

    @field_validator("name")
    @classmethod
    def _validate_safe_name(cls, value: str) -> str:
        if not _PLUGIN_NAME_RE.match(value):
            raise ValueError("Plugin name must be identifier-safe")
        return value


class PluginDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: str = ""
    scope: PluginScope = PluginScope.LOCAL
    severity: PluginDiagnosticSeverity
    component: PluginComponentKind | None = None
    path: Path | None = None
    message: str


class PluginComponentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: RequiredIdentifierStr
    scope: PluginScope = PluginScope.LOCAL
    root_dir: Path
    data_dir: Path
    path: Path
    user_config: dict[str, JsonValue] = Field(default_factory=dict)
    inline_config: dict[str, JsonValue] | None = None


class PluginSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str | None = None


class PluginSettingsSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: RequiredIdentifierStr
    scope: PluginScope = PluginScope.LOCAL
    root_dir: Path
    data_dir: Path
    path: Path
    user_config: dict[str, JsonValue] = Field(default_factory=dict)
    inline_config: dict[str, JsonValue] | None = None
    settings: PluginSettings


class PluginMonitorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RequiredIdentifierStr
    trigger: str = "always"
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    description: str = ""


class PluginComponentCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: int = 0
    roles: int = 0
    commands: int = 0
    hooks: int = 0
    mcp_servers: int = 0
    monitors: int = 0
    settings: int = 0


class PluginInstallSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PluginInstallSourceKind = PluginInstallSourceKind.LOCAL
    value: str
    ref: str = ""
    subdir: str = ""
    sha: str = ""
    adapter: str = ""
    marketplace: str = ""
    marketplace_provider: str = ""
    marketplace_source: str = ""
    marketplace_ref: str = ""
    requested_version: str | None = None


class PluginStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RequiredIdentifierStr
    version: str = "local"
    scope: PluginScope
    enabled: bool = True
    root_dir: Path
    source: PluginInstallSource
    user_config: dict[str, JsonValue] = Field(default_factory=dict)
    dependencies: tuple[PluginDependency, ...] = ()


class PluginStateFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: tuple[PluginStateRecord, ...] = ()


class PluginRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RequiredIdentifierStr
    version: str
    scope: PluginScope = PluginScope.LOCAL
    enabled: bool = True
    root_dir: Path
    data_dir: Path
    source: PluginInstallSource | None = None
    user_config: dict[str, JsonValue] = Field(default_factory=dict)
    manifest_path: Path | None = None
    manifest: PluginManifest
    skill_sources: tuple[PluginComponentSource, ...] = ()
    role_sources: tuple[PluginComponentSource, ...] = ()
    command_sources: tuple[PluginComponentSource, ...] = ()
    hook_sources: tuple[PluginComponentSource, ...] = ()
    mcp_sources: tuple[PluginComponentSource, ...] = ()
    monitor_sources: tuple[PluginComponentSource, ...] = ()
    monitor_definitions: tuple[PluginMonitorDefinition, ...] = ()
    settings_sources: tuple[PluginSettingsSource, ...] = ()
    component_counts: PluginComponentCounts = Field(
        default_factory=PluginComponentCounts
    )


class PluginRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: tuple[PluginRecord, ...] = ()
    diagnostics: tuple[PluginDiagnostic, ...] = ()

    def enabled_plugins(self) -> tuple[PluginRecord, ...]:
        return tuple(plugin for plugin in self.plugins if plugin.enabled)

    def skill_sources(self) -> tuple[PluginComponentSource, ...]:
        return tuple(
            source
            for plugin in self.enabled_plugins()
            for source in plugin.skill_sources
        )

    def role_sources(self) -> tuple[PluginComponentSource, ...]:
        return tuple(
            source
            for plugin in self.enabled_plugins()
            for source in plugin.role_sources
        )

    def hook_sources(self) -> tuple[PluginComponentSource, ...]:
        return tuple(
            source
            for plugin in self.enabled_plugins()
            for source in plugin.hook_sources
        )

    def command_sources(self) -> tuple[PluginComponentSource, ...]:
        return tuple(
            source
            for plugin in self.enabled_plugins()
            for source in plugin.command_sources
        )

    def mcp_sources(self) -> tuple[PluginComponentSource, ...]:
        return tuple(
            source for plugin in self.enabled_plugins() for source in plugin.mcp_sources
        )

    def monitor_sources(self) -> tuple[PluginComponentSource, ...]:
        return tuple(
            source
            for plugin in self.enabled_plugins()
            for source in plugin.monitor_sources
        )

    def settings_sources(self) -> tuple[PluginSettingsSource, ...]:
        return tuple(
            source
            for plugin in self.enabled_plugins()
            for source in plugin.settings_sources
        )
