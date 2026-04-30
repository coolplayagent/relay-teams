# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from relay_teams.logger import get_logger
from relay_teams.plugins.manifest_loader import load_plugin_record
from relay_teams.plugins.plugin_models import (
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginRecord,
    PluginRegistry,
    PluginScope,
)

LOGGER = get_logger(__name__)
_PLUGIN_DIRS_ENV_VAR = "RELAY_TEAMS_PLUGIN_DIRS"


class PluginConfigManager:
    def __init__(
        self,
        *,
        app_config_dir: Path,
        plugin_dirs: tuple[Path, ...] = (),
    ) -> None:
        self._app_config_dir = app_config_dir.expanduser().resolve()
        self._plugin_dirs = tuple(path.expanduser().resolve() for path in plugin_dirs)

    @classmethod
    def from_environment(
        cls,
        *,
        app_config_dir: Path,
    ) -> PluginConfigManager:
        return cls(
            app_config_dir=app_config_dir,
            plugin_dirs=_plugin_dirs_from_env(),
        )

    def load_registry(self) -> PluginRegistry:
        diagnostics: list[PluginDiagnostic] = []
        records: list[PluginRecord] = []
        seen_names: set[str] = set()
        data_root = self._app_config_dir / "plugins" / "data"
        for plugin_dir in self._plugin_dirs:
            if not plugin_dir.exists() or not plugin_dir.is_dir():
                diagnostics.append(
                    PluginDiagnostic(
                        scope=PluginScope.LOCAL,
                        severity=PluginDiagnosticSeverity.ERROR,
                        path=plugin_dir,
                        message="Plugin directory does not exist",
                    )
                )
                continue
            record, load_diagnostics = load_plugin_record(
                plugin_root=plugin_dir,
                data_root=data_root,
                manifest_config_dir_name=self._app_config_dir.name,
                scope=PluginScope.LOCAL,
            )
            diagnostics.extend(load_diagnostics)
            if record is not None:
                if record.name in seen_names:
                    diagnostics.append(
                        PluginDiagnostic(
                            plugin_name=record.name,
                            scope=record.scope,
                            severity=PluginDiagnosticSeverity.ERROR,
                            path=record.manifest_path or record.root_dir,
                            message=f"Duplicate plugin name skipped: {record.name}",
                        )
                    )
                    continue
                seen_names.add(record.name)
                records.append(record)
        return PluginRegistry(
            plugins=tuple(records),
            diagnostics=tuple(diagnostics),
        )


def _plugin_dirs_from_env() -> tuple[Path, ...]:
    raw_value = os.environ.get(_PLUGIN_DIRS_ENV_VAR, "").strip()
    if not raw_value:
        return ()
    paths: list[Path] = []
    for item in raw_value.split(os.pathsep):
        normalized = item.strip()
        if normalized:
            paths.append(Path(normalized))
    return tuple(paths)
