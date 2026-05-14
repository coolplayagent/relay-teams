# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

import pytest

from relay_teams.plugins.config_manager import PluginConfigManager
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceProviderKind,
    PluginMarketplaceSource,
)
from relay_teams.plugins.marketplace_service import PluginMarketplaceService
from relay_teams.plugins.plugin_models import PluginScope

_RUN_LIVE_ENV = "RELAY_TEAMS_RUN_CLAWHUB_LIVE_TESTS"
_LIVE_PACKAGE_ENV = "RELAY_TEAMS_CLAWHUB_LIVE_INSTALL_PACKAGE"
_LIVE_BASE_URL_ENV = "RELAY_TEAMS_CLAWHUB_LIVE_BASE_URL"


pytestmark = pytest.mark.skipif(
    os.environ.get(_RUN_LIVE_ENV) != "1",
    reason=f"Set {_RUN_LIVE_ENV}=1 to run ClawHub live plugin tests.",
)


def test_clawhub_live_marketplace_list_and_optional_install(tmp_path: Path) -> None:
    base_url = os.environ.get(_LIVE_BASE_URL_ENV, "https://clawhub.ai")
    source = PluginMarketplaceSource(
        provider=PluginMarketplaceProviderKind.CLAWHUB,
        name="clawhub",
        value=base_url,
    )
    service = PluginMarketplaceService()

    index = service.load_provider_index(
        source=source,
        app_config_dir=tmp_path / "app",
    )

    assert index.plugins
    assert index.plugins[0].name
    assert index.plugins[0].latest

    package_name = os.environ.get(_LIVE_PACKAGE_ENV, "").strip()
    if not package_name:
        pytest.skip(
            f"Set {_LIVE_PACKAGE_ENV} to exercise live artifact install as well."
        )

    entry = service.load_provider_entry(
        source=source,
        name=package_name,
        app_config_dir=tmp_path / "app",
    )
    selected = entry.selected_version(None)
    assert selected.source.value.startswith(f"{base_url.rstrip('/')}/api/v1/packages/")

    record = PluginConfigManager(
        app_config_dir=tmp_path / "app"
    ).install_marketplace_plugin(
        name=package_name,
        marketplace=Path("clawhub"),
        marketplace_provider=PluginMarketplaceProviderKind.CLAWHUB,
        marketplace_source=base_url,
        scope=PluginScope.USER,
        enabled=False,
    )

    assert record.name
    assert record.root_dir.exists()
