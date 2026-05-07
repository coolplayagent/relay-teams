# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.plugins.plugin_models import (
    PluginDependency,
    PluginInstallSource,
)
from relay_teams.validation import RequiredIdentifierStr


class PluginMarketplaceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source: PluginInstallSource
    sha256: str = ""
    dependencies: tuple[PluginDependency, ...] = ()


class PluginMarketplaceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: RequiredIdentifierStr
    description: str = ""
    latest: str = ""
    versions: tuple[PluginMarketplaceVersion, ...] = ()

    def selected_version(
        self,
        requested_version: str | None,
    ) -> PluginMarketplaceVersion:
        version = requested_version or self.latest
        if not version and self.versions:
            return max(
                self.versions,
                key=lambda candidate: _version_sort_key(candidate.version),
            )
        for item in self.versions:
            if item.version == version:
                return item
        raise ValueError(f"Marketplace plugin version not found: {self.name}@{version}")


class PluginMarketplaceIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    plugins: tuple[PluginMarketplaceEntry, ...] = Field(default_factory=tuple)

    def get_plugin(self, name: str) -> PluginMarketplaceEntry:
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        raise ValueError(f"Marketplace plugin not found: {name}")


def _version_sort_key(
    version: str,
) -> tuple[tuple[tuple[int, str], ...], int, tuple[tuple[int, str], ...]]:
    base_version, separator, prerelease = version.lower().partition("-")
    return (
        _version_parts(base_version),
        1 if not separator else 0,
        _version_parts(prerelease),
    )


def _version_parts(version: str) -> tuple[tuple[int, str], ...]:
    parts: list[tuple[int, str]] = []
    for part in re.findall(r"\d+|[A-Za-z]+", version):
        if part.isdigit():
            parts.append((0, f"{int(part):020d}"))
            continue
        parts.append((1, part))
    return tuple(parts)
