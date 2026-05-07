# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from relay_teams.plugins.marketplace_models import PluginMarketplaceIndex


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
