# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from relay_teams.plugins.clawhub_marketplace_provider import ClawHubMarketplaceProvider
from relay_teams.plugins.claude_marketplace_provider import ClaudeMarketplaceProvider
from relay_teams.plugins.marketplace_models import PluginMarketplaceIndex
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceEntry,
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
)
from relay_teams.plugins.marketplace_policy import (
    PluginMarketplaceInstallPolicy,
    apply_install_policy_to_entry,
    apply_install_policy_to_index,
    load_plugin_marketplace_install_policy,
)


class PluginMarketplaceService:
    @staticmethod
    def load_index(source: Path) -> PluginMarketplaceIndex:
        resolved_source = source.expanduser().resolve()
        try:
            raw = json.loads(resolved_source.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid marketplace JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Marketplace JSON must be an object")
        try:
            return PluginMarketplaceIndex.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid marketplace index: {exc}") from exc

    def load_provider_index(
        self,
        *,
        source: PluginMarketplaceSource,
        app_config_dir: Path,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
        limit: int = 100,
        cursor: str = "",
        include_details: bool = False,
        fetch_all: bool = True,
    ) -> PluginMarketplaceIndex:
        if source.provider == PluginMarketplaceProviderKind.LOCAL_JSON:
            return self.load_index(Path(source.value))
        if source.provider == PluginMarketplaceProviderKind.CLAUDE:
            return ClaudeMarketplaceProvider().load_index(
                source=source,
                app_config_dir=app_config_dir,
            )
        if source.provider == PluginMarketplaceProviderKind.CLAWHUB:
            policy = install_policy or load_plugin_marketplace_install_policy(
                app_config_dir
            )
            clawhub_cursor, clawhub_fetch_all = _clawhub_full_load_options(
                cursor=cursor,
                fetch_all=fetch_all,
            )
            index = ClawHubMarketplaceProvider().load_index(
                source=source,
                limit=limit,
                cursor=clawhub_cursor,
                fetch_all=clawhub_fetch_all,
                include_versions=include_details,
            )
            if not include_details:
                return index
            return apply_install_policy_to_index(
                index=index,
                provider=source.provider,
                policy=policy,
            )
        raise ValueError(f"Unsupported plugin marketplace provider: {source.provider}")

    def load_provider_entry(
        self,
        *,
        source: PluginMarketplaceSource,
        name: str,
        app_config_dir: Path,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
    ) -> PluginMarketplaceEntry:
        if source.provider == PluginMarketplaceProviderKind.CLAWHUB:
            policy = install_policy or load_plugin_marketplace_install_policy(
                app_config_dir
            )
            return apply_install_policy_to_entry(
                entry=ClawHubMarketplaceProvider().load_entry(source=source, name=name),
                provider=source.provider,
                policy=policy,
            )
        return self.load_provider_index(
            source=source,
            app_config_dir=app_config_dir,
        ).get_plugin(name)

    def search_provider_index(
        self,
        *,
        source: PluginMarketplaceSource,
        query: str,
        app_config_dir: Path,
        install_policy: PluginMarketplaceInstallPolicy | None = None,
        include_details: bool = False,
    ) -> PluginMarketplaceIndex:
        normalized_query = query.strip()
        if source.provider == PluginMarketplaceProviderKind.CLAWHUB:
            policy = install_policy or load_plugin_marketplace_install_policy(
                app_config_dir
            )
            index = ClawHubMarketplaceProvider().search_index(
                source=source,
                query=normalized_query,
                include_versions=include_details,
            )
            if not include_details:
                return index
            return apply_install_policy_to_index(
                index=index,
                provider=source.provider,
                policy=policy,
            )
        index = self.load_provider_index(
            source=source,
            app_config_dir=app_config_dir,
        )
        if not normalized_query:
            return index
        lowered_query = normalized_query.lower()
        return PluginMarketplaceIndex(
            version=index.version,
            plugins=tuple(
                plugin
                for plugin in index.plugins
                if lowered_query in plugin.name.lower()
                or lowered_query in plugin.description.lower()
            ),
        )


def _clawhub_full_load_options(*, cursor: str, fetch_all: bool) -> tuple[str, bool]:
    if cursor.strip() or not fetch_all:
        return "", True
    return cursor, fetch_all
