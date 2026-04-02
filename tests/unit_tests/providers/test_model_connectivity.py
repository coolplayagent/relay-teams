# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest

from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
    SamplingConfig,
)
from agent_teams.providers.model_connectivity import (
    ModelDiscoveryRequest,
    ModelConnectivityProbeOverride,
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeService,
)
from agent_teams.sessions.runs.runtime_config import RuntimeConfig, RuntimePaths


class _FakeHttpClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        error: BaseException | None = None,
        captured: dict[str, object] | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._captured = captured if captured is not None else {}

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: object,
    ) -> httpx.Response:
        self._captured["url"] = url
        self._captured["headers"] = dict(headers)
        self._captured["json"] = json
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        self._captured["url"] = url
        self._captured["headers"] = dict(headers)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def test_probe_uses_saved_profile_and_returns_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={
                        "id": "cmpl-test",
                        "usage": {
                            "prompt_tokens": 8,
                            "completion_tokens": 1,
                            "total_tokens": 9,
                        },
                    },
                ),
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(profile_name="default", timeout_ms=3200)
    )

    assert result.ok is True
    assert result.provider == ProviderType.OPENAI_COMPATIBLE
    assert result.token_usage is not None
    assert result.token_usage.total_tokens == 9
    assert captured["url"] == "https://example.test/v1/chat/completions"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    assert captured["timeout_seconds"] == pytest.approx(3.2)
    assert captured["connect_timeout_seconds"] == pytest.approx(3.2)
    payload = cast(dict[str, object], captured["json"])
    assert payload["temperature"] == pytest.approx(1.0)
    assert payload["top_p"] == pytest.approx(0.95)


def test_probe_uses_profile_connect_timeout_when_request_timeout_omitted(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is True
    assert captured["timeout_seconds"] == pytest.approx(17.5)


def test_probe_merges_override_with_saved_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            profile_name="default",
            override=ModelConnectivityProbeOverride(
                model="draft-model",
                base_url="https://draft.test/v1",
            ),
        )
    )

    assert result.ok is True
    assert result.model == "draft-model"
    assert captured["url"] == "https://draft.test/v1/chat/completions"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    payload = cast(dict[str, object], captured["json"])
    assert payload["model"] == "draft-model"


def test_probe_uses_model_ssl_override_before_global_default(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            profile_name="default",
            override=ModelConnectivityProbeOverride(ssl_verify=False),
        )
    )

    assert result.ok is True
    assert captured["ssl_verify"] is False


def test_probe_returns_timeout_error(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(error=httpx.ReadTimeout("timed out")),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(profile_name="default", timeout_ms=2000)
    )

    assert result.ok is False
    assert result.error_code == "network_timeout"
    assert result.retryable is True
    assert result.diagnostics.endpoint_reachable is False


def test_probe_returns_auth_error_for_unauthorized_response(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                401,
                json={"error": {"message": "Invalid API key."}},
            )
        ),
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is False
    assert result.error_code == "auth_invalid"
    assert result.retryable is False
    assert result.diagnostics.auth_valid is False
    assert result.error_message == "Invalid API key."


def test_probe_accepts_editor_default_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                model="draft-model",
                base_url="https://draft.test/v1",
                api_key="draft-api-key",
            ),
            timeout_ms=15000,
        )
    )

    assert result.ok is True
    assert captured["url"] == "https://draft.test/v1/chat/completions"
    assert captured["timeout_seconds"] == pytest.approx(15.0)


def test_probe_supports_bigmodel_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.BIGMODEL,
                model="glm-4.5",
                base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                api_key="draft-api-key",
            )
        )
    )

    assert result.ok is True
    assert result.provider == ProviderType.BIGMODEL
    assert (
        captured["url"]
        == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    )


def test_probe_allows_header_only_override(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                model="draft-model",
                base_url="https://draft.test/v1",
                headers=(
                    ModelRequestHeader(
                        name="Authorization",
                        value="Bearer header-only",
                    ),
                ),
            )
        )
    )

    assert result.ok is True
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer header-only"


def test_discover_models_uses_saved_profile_and_parses_catalog(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "data": [
                            {"id": "reasoning-model"},
                            {"id": "fake-chat-model"},
                            {"id": "fake-chat-model"},
                        ],
                    },
                ),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(profile_name="default", timeout_ms=2800)
    )

    assert result.ok is True
    assert result.provider == ProviderType.OPENAI_COMPATIBLE
    assert result.models == ("fake-chat-model", "reasoning-model")
    assert captured["url"] == "https://example.test/v1/models"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    assert captured["timeout_seconds"] == pytest.approx(2.8)
    assert captured["connect_timeout_seconds"] == pytest.approx(2.8)
    assert tuple(entry.model for entry in result.model_entries) == (
        "fake-chat-model",
        "reasoning-model",
    )


def test_discover_models_extracts_context_window_when_provider_returns_it(
    monkeypatch,
) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "fake-chat-model",
                            "context_window": 256000,
                        },
                        {
                            "id": "reasoning-model",
                            "limits": {
                                "context": 128000,
                            },
                        },
                    ],
                },
            )
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is True
    assert result.models == ("fake-chat-model", "reasoning-model")
    assert result.model_entries[0].model == "fake-chat-model"
    assert result.model_entries[0].context_window == 256000
    assert result.model_entries[1].model == "reasoning-model"
    assert result.model_entries[1].context_window == 128000


def test_discover_models_falls_back_to_known_context_window_rules(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "gpt-4o-mini"},
                        {"id": "kimi-k2.5"},
                    ],
                },
            )
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is True
    assert result.models == ("gpt-4o-mini", "kimi-k2.5")
    assert result.model_entries[0].context_window == 128000
    assert result.model_entries[1].context_window == 256000


def test_discover_models_allows_saved_api_key_with_override_base_url(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"data": [{"id": "draft-model"}]}),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            profile_name="default",
            override=ModelConnectivityProbeOverride(base_url="https://draft.test/v1"),
        )
    )

    assert result.ok is True
    assert result.models == ("draft-model",)
    assert captured["url"] == "https://draft.test/v1/models"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    assert captured["timeout_seconds"] == pytest.approx(17.5)


def test_discover_models_supports_bigmodel_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"data": [{"id": "glm-4.5"}]}),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.BIGMODEL,
                base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                api_key="draft-api-key",
            )
        )
    )

    assert result.ok is True
    assert result.provider == ProviderType.BIGMODEL
    assert result.models == ("glm-4.5",)
    assert captured["url"] == "https://open.bigmodel.cn/api/coding/paas/v4/models"


def test_discover_models_allows_header_only_override(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"data": [{"id": "draft-model"}]}),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                base_url="https://draft.test/v1",
                headers=(
                    ModelRequestHeader(
                        name="Authorization",
                        value="Bearer discovery-header",
                    ),
                ),
            )
        )
    )

    assert result.ok is True
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer discovery-header"


def test_discover_models_returns_invalid_response_error(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(200, json={"items": [{"id": "missing-data"}]})
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is False
    assert result.error_code == "invalid_response"
    assert result.retryable is False


def test_probe_requires_source_config() -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    with pytest.raises(ValueError, match="Provide profile_name, override, or both."):
        service.probe(ModelConnectivityProbeRequest())


def test_discover_models_requires_source_config() -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    with pytest.raises(ValueError, match="Provide profile_name, override, or both."):
        service.discover_models(ModelDiscoveryRequest())


def test_probe_resolves_default_alias_to_runtime_default_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(
        get_runtime=lambda: _runtime_config(
            profile_name="kimi",
            default_model_profile="kimi",
        )
    )

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is True
    assert captured["url"] == "https://example.test/v1/chat/completions"


def _runtime_config(
    *,
    profile_name: str = "default",
    default_model_profile: str | None = None,
) -> RuntimeConfig:
    config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="saved-model",
        base_url="https://example.test/v1",
        api_key="saved-api-key",
        ssl_verify=True,
        sampling=SamplingConfig(
            temperature=1.0,
            top_p=0.95,
            max_tokens=128,
        ),
        connect_timeout_seconds=17.5,
    )
    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=Path("D:/tmp/.agent_teams"),
            env_file=Path("D:/tmp/.agent_teams/.env"),
            db_path=Path("D:/tmp/.agent_teams/agent_teams.db"),
            roles_dir=Path("D:/tmp/.agent_teams/roles"),
        ),
        llm_profiles={profile_name: config},
        default_model_profile=default_model_profile or profile_name,
    )
