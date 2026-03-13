# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest

from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderType,
    SamplingConfig,
)
from agent_teams.providers.model_connectivity import (
    ModelConnectivityProbeOverride,
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeService,
)
from agent_teams.runs.runtime_config import RuntimeConfig, RuntimePaths


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


def test_probe_uses_saved_profile_and_returns_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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
        "agent_teams.providers.model_connectivity.create_proxy_http_client",
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


def test_probe_requires_source_config() -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    with pytest.raises(ValueError, match="Provide profile_name, override, or both."):
        service.probe(ModelConnectivityProbeRequest())


def _runtime_config() -> RuntimeConfig:
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
        llm_profiles={"default": config},
    )
