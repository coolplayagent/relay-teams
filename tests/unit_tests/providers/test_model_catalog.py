# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.providers import model_catalog
from relay_teams.providers.model_config import ProviderType
from relay_teams.providers.model_catalog import ModelCatalogService


class _FakeCatalogClient:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self._response = response
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> "_FakeCatalogClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        self.requested_urls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.asyncio
async def test_model_catalog_fetches_models_dev_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "id": "openai",
                    "name": "OpenAI",
                    "api": "https://api.openai.com/v1",
                    "env": ["OPENAI_API_KEY"],
                    "models": {
                        "gpt-4o": {
                            "id": "gpt-4o",
                            "name": "GPT-4o",
                            "attachment": True,
                            "reasoning": False,
                            "temperature": True,
                            "tool_call": True,
                            "release_date": "2024-05-13",
                            "last_updated": "2024-05-13",
                            "modalities": {
                                "input": ["text", "image"],
                                "output": ["text"],
                            },
                            "limit": {"context": 128000, "output": 16384},
                        }
                    },
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=ProxyEnvConfig,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert client.requested_urls == ["https://models.dev/api.json"]
    assert len(result.providers) == 1
    provider = result.providers[0]
    assert provider.id == "openai"
    assert provider.runtime_provider == ProviderType.OPENAI_COMPATIBLE
    assert provider.api == "https://api.openai.com/v1"
    assert provider.env == ("OPENAI_API_KEY",)
    assert provider.models[0].id == "gpt-4o"
    assert provider.models[0].context_window == 128000
    assert provider.models[0].input_modalities[0].value == "image"


@pytest.mark.asyncio
async def test_model_catalog_marks_anthropic_compatible_marketplace_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "minimax": {
                    "name": "MiniMax",
                    "npm": "@ai-sdk/anthropic",
                    "api": "https://api.minimax.io/anthropic/v1",
                    "models": {
                        "MiniMax-M2.7": {
                            "name": "MiniMax-M2.7",
                            "limit": {"context": 204800, "output": 131072},
                        }
                    },
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=ProxyEnvConfig,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert result.providers[0].runtime_provider == ProviderType.ANTHROPIC
    assert result.providers[0].models[0].context_window == 204800


@pytest.mark.asyncio
async def test_model_catalog_marks_anthropic_api_path_as_anthropic_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "custom-provider": {
                    "name": "Custom Anthropic",
                    "api": "https://api.example.test/anthropic/v1",
                    "models": {"custom-claude": {"name": "Custom Claude"}},
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=ProxyEnvConfig,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert result.providers[0].runtime_provider == ProviderType.ANTHROPIC


@pytest.mark.asyncio
async def test_model_catalog_marks_anthropic_api_root_as_anthropic_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "custom-provider": {
                    "name": "Custom Anthropic",
                    "api": "https://api.example.test/anthropic",
                    "models": {"custom-claude": {"name": "Custom Claude"}},
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=ProxyEnvConfig,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert result.providers[0].runtime_provider == ProviderType.ANTHROPIC


@pytest.mark.asyncio
async def test_model_catalog_uses_proxy_config_and_30_second_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {"gpt-4o": {"name": "GPT-4o"}},
                }
            },
        )
    )
    proxy_config = ProxyEnvConfig(https_proxy="http://proxy.example:8443")
    captured_kwargs: list[dict[str, object]] = []

    def create_client(**kwargs: object) -> _FakeCatalogClient:
        captured_kwargs.append(kwargs)
        return client

    monkeypatch.setattr(model_catalog, "create_async_http_client", create_client)
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: proxy_config,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["proxy_config"] == proxy_config
    assert captured_kwargs[0]["timeout_seconds"] == 30.0
    assert captured_kwargs[0]["connect_timeout_seconds"] == 30.0


@pytest.mark.asyncio
async def test_model_catalog_returns_fetched_data_when_cache_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "model-catalog-cache-dir"
    cache_dir.write_text("not a directory", encoding="utf-8")
    client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {"gpt-4o": {"name": "GPT-4o"}},
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=cache_dir,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert result.providers[0].models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_model_catalog_retries_transient_network_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = [
        _FakeCatalogClient(httpx.ConnectError("connection reset")),
        _FakeCatalogClient(
            httpx.Response(
                200,
                json={
                    "openai": {
                        "name": "OpenAI",
                        "models": {"gpt-4o": {"name": "GPT-4o"}},
                    }
                },
            )
        ),
    ]
    created_clients: list[_FakeCatalogClient] = []

    def create_client(**_kwargs: object) -> _FakeCatalogClient:
        client = clients[len(created_clients)]
        created_clients.append(client)
        return client

    monkeypatch.setattr(model_catalog, "create_async_http_client", create_client)
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert len(created_clients) == 2
    assert result.providers[0].models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_model_catalog_uses_fresh_cache_without_fetching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )
    first_client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {
                        "gpt-4o": {
                            "name": "GPT-4o",
                            "limit": {"context": 128000, "output": 16384},
                        }
                    },
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: first_client,
    )
    assert (await service.get_catalog_async(refresh=True)).ok is True

    def fail_fetch(**_kwargs: object) -> _FakeCatalogClient:
        raise AssertionError("fresh cache should avoid network")

    monkeypatch.setattr(model_catalog, "create_async_http_client", fail_fetch)

    cached = await service.get_catalog_async()

    assert cached.ok is True
    assert cached.cache_age_seconds is not None
    assert cached.providers[0].models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_model_catalog_returns_stale_cache_when_refresh_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
        ttl_seconds=0,
    )
    success_client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {"gpt-4o": {"name": "GPT-4o"}},
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: success_client,
    )
    assert (await service.get_catalog_async(refresh=True)).ok is True

    failed_client = _FakeCatalogClient(httpx.ConnectError("offline"))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: failed_client,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.stale is True
    assert result.error_code == "network_error"
    assert result.providers[0].models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_model_catalog_prefers_stale_cache_without_fetching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
        ttl_seconds=0,
    )
    success_client = _FakeCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {"gpt-4o": {"name": "GPT-4o"}},
                }
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: success_client,
    )
    assert (await service.get_catalog_async(refresh=True)).ok is True

    def fail_fetch(**_kwargs: object) -> _FakeCatalogClient:
        raise AssertionError("cached catalog should be returned before refresh")

    monkeypatch.setattr(model_catalog, "create_async_http_client", fail_fetch)

    result = await service.get_catalog_async()

    assert result.ok is True
    assert result.stale is True
    assert result.providers[0].models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_model_catalog_without_cache_reports_fetch_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_client = _FakeCatalogClient(httpx.ConnectError("offline"))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: failed_client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.providers == ()
    assert result.error_code == "network_error"


# -- async catalog client helper and tests --


class _FakeAsyncCatalogClient:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self._response = response
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> _FakeAsyncCatalogClient:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        self.requested_urls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.asyncio
async def test_get_catalog_async_fetches_from_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "api": "https://api.openai.com/v1",
                    "env": ["OPENAI_API_KEY"],
                    "models": {
                        "gpt-4o": {
                            "name": "GPT-4o",
                        },
                    },
                },
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=ProxyEnvConfig,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is True
    assert len(result.providers) == 1
    assert result.providers[0].id == "openai"
    assert client.requested_urls == ["https://models.dev/api.json"]


@pytest.mark.asyncio
async def test_get_catalog_async_returns_cached_without_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_client = _FakeAsyncCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {"gpt-4o": {"name": "GPT-4o"}},
                },
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: async_client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
        ttl_seconds=0,
    )

    first = await service.get_catalog_async(refresh=True)
    assert first.ok is True

    def fail_async(**_kwargs: object) -> None:
        raise AssertionError("should use cache")

    monkeypatch.setattr(model_catalog, "create_async_http_client", fail_async)

    cached = await service.get_catalog_async()
    assert cached.ok is True
    assert cached.stale is True


@pytest.mark.asyncio
async def test_get_catalog_async_handles_network_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncCatalogClient(httpx.ConnectError("offline"))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.error_code == "network_error"


@pytest.mark.asyncio
async def test_get_catalog_async_handles_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncCatalogClient(httpx.ReadTimeout("timeout"))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.error_code == "network_timeout"


@pytest.mark.asyncio
async def test_get_catalog_async_handles_http_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncCatalogClient(httpx.Response(500))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.error_code == "http_error"


@pytest.mark.asyncio
async def test_get_catalog_async_handles_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncCatalogClient(
        httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"})
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.error_code == "invalid_response"


@pytest.mark.asyncio
async def test_get_catalog_async_handles_empty_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeAsyncCatalogClient(httpx.Response(200, json={}))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: client,
    )
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.error_code == "invalid_response"


@pytest.mark.asyncio
async def test_get_catalog_async_returns_stale_on_fetch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ModelCatalogService(
        config_dir=tmp_path,
        get_proxy_config=lambda: ProxyEnvConfig(),
        ttl_seconds=0,
    )
    success_client = _FakeAsyncCatalogClient(
        httpx.Response(
            200,
            json={
                "openai": {
                    "name": "OpenAI",
                    "models": {"gpt-4o": {"name": "GPT-4o"}},
                },
            },
        )
    )
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: success_client,
    )
    assert (await service.get_catalog_async(refresh=True)).ok is True

    failed_client = _FakeAsyncCatalogClient(httpx.ConnectError("offline"))
    monkeypatch.setattr(
        model_catalog,
        "create_async_http_client",
        lambda **_kwargs: failed_client,
    )

    result = await service.get_catalog_async(refresh=True)

    assert result.ok is False
    assert result.stale is True
    assert result.error_code == "network_error"
    assert result.providers[0].models[0].id == "gpt-4o"
