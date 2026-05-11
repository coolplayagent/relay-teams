# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.plugins.plugin_models import (
    PluginDependency,
    PluginInstallSource,
    PluginInstallSourceKind,
)
from relay_teams.validation import RequiredIdentifierStr


class PluginMarketplaceProviderKind(str, Enum):
    LOCAL_JSON = "local_json"
    CLAUDE = "claude"


class PluginMarketplaceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: PluginMarketplaceProviderKind = PluginMarketplaceProviderKind.LOCAL_JSON
    name: str = ""
    value: str = ""
    ref: str = ""
    refresh: bool = False


class PluginMarketplaceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source: PluginInstallSource
    sha256: str = ""
    dependencies: tuple[PluginDependency, ...] = ()
    warnings: tuple[str, ...] = ()
    unsupported_reason: str = ""


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
        if requested_version:
            for item in self.versions:
                if item.version == requested_version:
                    return item
            raise ValueError(
                f"Marketplace plugin version not found: {self.name}@{requested_version}"
            )
        candidates = self.supported_versions()
        version = self.latest
        if version:
            for item in candidates:
                if item.version == version:
                    return item
            if candidates:
                return max(
                    candidates,
                    key=lambda candidate: _version_sort_key(candidate.version),
                )
            for item in self.versions:
                if item.version == version:
                    return item
        if candidates:
            return max(
                candidates,
                key=lambda candidate: _version_sort_key(candidate.version),
            )
        if self.versions:
            return max(
                self.versions,
                key=lambda candidate: _version_sort_key(candidate.version),
            )
        for item in self.versions:
            if item.version == version:
                return item
        raise ValueError(f"Marketplace plugin version not found: {self.name}@{version}")

    def supported_versions(self) -> tuple[PluginMarketplaceVersion, ...]:
        return tuple(
            version
            for version in self.versions
            if not version.unsupported_reason
            and version.source.kind != PluginInstallSourceKind.UNSUPPORTED
        )


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
