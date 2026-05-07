# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from relay_teams.providers.model_config import (
    ModelConfigPayload,
    default_model_fallback_config,
)
from relay_teams.providers.model_catalog import ModelCatalogResult, ModelCatalogService
from relay_teams.providers.model_config_manager import ModelConfigManager
from relay_teams.providers.model_connectivity import (
    CodeAgentAuthVerifyResult,
    ModelConnectivityProbeService,
)
from relay_teams.providers.model_config_service import ModelConfigService
from relay_teams.providers.model_fallback_config_manager import (
    ModelFallbackConfigManager,
)
from relay_teams.sessions.runs.runtime_config import RuntimeConfig


class _RecordingModelConfigManager:
    def __init__(self) -> None:
        self.saved_config: dict[str, object] | None = None

    def get_model_config(self) -> dict[str, object]:
        return {}

    def get_model_profiles(self) -> dict[str, dict[str, object]]:
        return {}

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, object],
        *,
        source_name: str | None = None,
    ) -> None:
        raise NotImplementedError

    def delete_model_profile(self, name: str) -> None:
        raise NotImplementedError

    def save_model_config(self, config: dict[str, object]) -> None:
        self.saved_config = config


class _RecordingModelFallbackConfigManager:
    def get_model_fallback_config(self) -> object:
        return default_model_fallback_config()

    def save_model_fallback_config(self, config: object) -> None:
        _ = config
        return None


class _RecordingModelCatalogService:
    async def get_catalog_async(self, *, refresh: bool = False) -> ModelCatalogResult:
        return ModelCatalogResult(
            ok=True,
            source_url="https://models.dev/api.json",
            providers=(),
            stale=refresh,
        )


def test_save_model_config_preserves_omitted_optional_fields() -> None:
    manager = _RecordingModelConfigManager()
    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, manager),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )

    service.save_model_config(
        ModelConfigPayload.model_validate(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4.1",
                    "base_url": "https://example.test/v1",
                    "temperature": 0.2,
                    "top_p": 1.0,
                },
                "kimi": {
                    "provider": "openai_compatible",
                    "model": "kimi-k2.5",
                    "base_url": "https://example.test/v1",
                    "temperature": 0.2,
                    "top_p": 1.0,
                    "max_tokens": None,
                },
            }
        )
    )

    assert manager.saved_config == {
        "default": {
            "provider": "openai_compatible",
            "model": "gpt-4.1",
            "base_url": "https://example.test/v1",
            "temperature": 0.2,
            "top_p": 1.0,
        },
        "kimi": {
            "provider": "openai_compatible",
            "model": "kimi-k2.5",
            "base_url": "https://example.test/v1",
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": None,
        },
    }


@pytest.mark.asyncio
async def test_verify_codeagent_auth_async_delegates_to_probe_service() -> None:
    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, _RecordingModelConfigManager()),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )
    expected = CodeAgentAuthVerifyResult(
        status="valid",
        checked_at=datetime(2026, 4, 27, 2, 0, tzinfo=UTC),
        detail=None,
    )
    captured: dict[str, object] = {}

    class _FakeProbeService:
        async def verify_codeagent_auth_async(
            self, *, profile_name: str
        ) -> CodeAgentAuthVerifyResult:
            captured["profile_name"] = profile_name
            return expected

    service._model_connectivity_probe_service = cast(
        ModelConnectivityProbeService, _FakeProbeService()
    )

    result = await service.verify_codeagent_auth_async(profile_name="default")

    assert captured["profile_name"] == "default"
    assert result == expected


@pytest.mark.asyncio
async def test_get_model_catalog_async_delegates() -> None:
    from relay_teams.providers.model_catalog import ModelCatalogResult
    from unittest.mock import AsyncMock, MagicMock

    mock_catalog = MagicMock(spec=ModelCatalogService)
    expected = ModelCatalogResult(ok=True, source_url="test", providers=())
    mock_catalog.get_catalog_async = AsyncMock(return_value=expected)

    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, _RecordingModelConfigManager()),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=mock_catalog,
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )

    result = await service.get_model_catalog_async(refresh=True)

    mock_catalog.get_catalog_async.assert_awaited_once_with(refresh=True)
    assert result == expected


@pytest.mark.asyncio
async def test_probe_connectivity_async_delegates() -> None:
    from relay_teams.providers.model_connectivity import (
        ModelConnectivityProbeRequest,
        ModelConnectivityProbeService,
    )
    from unittest.mock import AsyncMock, MagicMock

    expected = MagicMock()
    mock_probe = MagicMock(spec=ModelConnectivityProbeService)
    mock_probe.probe_async = AsyncMock(return_value=expected)

    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, _RecordingModelConfigManager()),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )
    service._model_connectivity_probe_service = mock_probe

    request = ModelConnectivityProbeRequest(profile_name="default")
    result = await service.probe_connectivity_async(request)

    mock_probe.probe_async.assert_awaited_once_with(request)
    assert result == expected


@pytest.mark.asyncio
async def test_discover_models_async_delegates() -> None:
    from relay_teams.providers.model_connectivity import (
        ModelConnectivityProbeService,
        ModelDiscoveryRequest,
    )
    from unittest.mock import AsyncMock, MagicMock

    expected = MagicMock()
    mock_probe = MagicMock(spec=ModelConnectivityProbeService)
    mock_probe.discover_models_async = AsyncMock(return_value=expected)

    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, _RecordingModelConfigManager()),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )
    service._model_connectivity_probe_service = mock_probe

    request = ModelDiscoveryRequest(profile_name="default")
    result = await service.discover_models_async(request)

    mock_probe.discover_models_async.assert_awaited_once_with(request)
    assert result == expected


@pytest.mark.asyncio
async def test_verify_codeagent_auth_async_delegates() -> None:
    from unittest.mock import AsyncMock, MagicMock

    expected = CodeAgentAuthVerifyResult(
        status="valid",
        checked_at=datetime(2026, 4, 27, 2, 0, tzinfo=UTC),
        detail=None,
    )
    mock_probe = MagicMock(spec=ModelConnectivityProbeService)
    mock_probe.verify_codeagent_auth_async = AsyncMock(return_value=expected)

    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, _RecordingModelConfigManager()),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )
    service._model_connectivity_probe_service = mock_probe

    result = await service.verify_codeagent_auth_async(profile_name="default")

    mock_probe.verify_codeagent_auth_async.assert_awaited_once_with(
        profile_name="default"
    )
    assert result == expected
