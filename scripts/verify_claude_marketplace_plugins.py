# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import tempfile

from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
)
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.plugin_models import PluginScope

_DEFAULT_MARKETPLACE_NAME = "claude-plugins-official"
_DEFAULT_MARKETPLACE_SOURCE = "anthropics/claude-plugins-official"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Claude official marketplace parsing and optional installs."
    )
    parser.add_argument("--source", default=_DEFAULT_MARKETPLACE_SOURCE)
    parser.add_argument("--name", default=_DEFAULT_MARKETPLACE_NAME)
    parser.add_argument("--ref", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--plugins", default="")
    parser.add_argument("--app-config-dir", default="")
    args = parser.parse_args()

    app_config_dir = (
        Path(args.app_config_dir).expanduser().resolve()
        if args.app_config_dir
        else Path(tempfile.mkdtemp(prefix="relay-teams-claude-marketplace-"))
    )
    source = PluginMarketplaceSource(
        provider=PluginMarketplaceProviderKind.CLAUDE,
        name=args.name,
        value=args.source,
        ref=args.ref,
        refresh=args.refresh,
    )
    index = PluginMarketplaceService().load_provider_index(
        source=source,
        app_config_dir=app_config_dir,
    )
    versions = [version for plugin in index.plugins for version in plugin.versions]
    kind_counts = Counter(version.source.kind.value for version in versions)
    unsupported = [
        plugin.name
        for plugin in index.plugins
        for version in plugin.versions
        if version.unsupported_reason
    ]
    warnings = [
        plugin.name
        for plugin in index.plugins
        for version in plugin.versions
        if version.warnings
    ]
    print(f"app_config_dir={app_config_dir}", flush=True)
    print(f"plugins={len(index.plugins)} versions={len(versions)}", flush=True)
    print(
        "source_kinds="
        + ",".join(f"{key}:{value}" for key, value in sorted(kind_counts.items())),
        flush=True,
    )
    print(f"unsupported={len(unsupported)}", flush=True)
    if unsupported:
        print("unsupported_names=" + ",".join(unsupported), flush=True)
    print(f"warnings={len(warnings)}", flush=True)
    if warnings:
        print("warning_names=" + ",".join(warnings), flush=True)
    if args.install:
        _install_plugins(
            app_config_dir=app_config_dir,
            marketplace_name=args.name,
            marketplace_source=args.source,
            marketplace_ref=args.ref,
            plugin_names=tuple(plugin.name for plugin in index.plugins),
            requested_plugins=_requested_plugins(args.plugins),
            start=max(args.start, 0),
            limit=max(args.limit, 0),
        )
    return 0


def _install_plugins(
    *,
    app_config_dir: Path,
    marketplace_name: str,
    marketplace_source: str,
    marketplace_ref: str,
    plugin_names: tuple[str, ...],
    requested_plugins: tuple[str, ...],
    start: int,
    limit: int,
) -> None:
    manager = PluginConfigManager(app_config_dir=app_config_dir)
    failures: list[tuple[str, str]] = []
    remaining_names = requested_plugins or plugin_names[start:]
    selected_names = remaining_names[:limit] if limit > 0 else remaining_names
    print(
        f"install_range=start:{start},count:{len(selected_names)}",
        flush=True,
    )
    for name in selected_names:
        try:
            record = manager.install_marketplace_plugin(
                name=name,
                marketplace=Path(marketplace_name),
                marketplace_provider=PluginMarketplaceProviderKind.CLAUDE,
                marketplace_source=marketplace_source,
                marketplace_ref=marketplace_ref,
                scope=PluginScope.USER,
            )
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"install_failed name={name} error={exc}", flush=True)
            continue
        print(f"installed name={record.name} version={record.version}", flush=True)
    print(f"install_failures={len(failures)}", flush=True)


def _requested_plugins(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


if __name__ == "__main__":
    raise SystemExit(main())
