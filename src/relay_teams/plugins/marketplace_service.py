# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from relay_teams.plugins.claude_marketplace_provider import ClaudeMarketplaceProvider
from relay_teams.plugins.marketplace_models import PluginMarketplaceIndex
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
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
    ) -> PluginMarketplaceIndex:
        if source.provider == PluginMarketplaceProviderKind.LOCAL_JSON:
            return self.load_index(Path(source.value))
        if source.provider == PluginMarketplaceProviderKind.CLAUDE:
            return ClaudeMarketplaceProvider().load_index(
                source=source,
                app_config_dir=app_config_dir,
            )
        raise ValueError(f"Unsupported plugin marketplace provider: {source.provider}")
