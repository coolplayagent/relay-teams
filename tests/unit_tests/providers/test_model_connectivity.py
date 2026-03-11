# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
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


def test_probe_uses_saved_profile_and_returns_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    def fake_post(
        url: str,
        *,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
    ) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["json"] = json
        captured["timeout"] = timeout
        return httpx.Response(
            200,
            json={
                "id": "cmpl-test",
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "total_tokens": 9,
                },
            },
        )

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.httpx.post", fake_post
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
    assert captured["timeout"] == pytest.approx(3.2)
    payload = cast(dict[str, object], captured["json"])
    assert payload["temperature"] == pytest.approx(1.0)
    assert payload["top_p"] == pytest.approx(0.95)


def test_probe_merges_override_with_saved_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    def fake_post(
        url: str,
        *,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
    ) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["json"] = json
        captured["timeout"] = timeout
        return httpx.Response(200, json={"usage": {}})

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.httpx.post", fake_post
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


def test_probe_returns_timeout_error(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    def fake_post(
        url: str,
        *,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
    ) -> httpx.Response:
        _ = (url, headers, json, timeout)
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.httpx.post", fake_post
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

    def fake_post(
        url: str,
        *,
        headers: Mapping[str, str],
        json: object,
        timeout: float,
    ) -> httpx.Response:
        _ = (url, headers, json, timeout)
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API key."}},
        )

    monkeypatch.setattr(
        "agent_teams.providers.model_connectivity.httpx.post", fake_post
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is False
    assert result.error_code == "auth_invalid"
    assert result.retryable is False
    assert result.diagnostics.auth_valid is False
    assert result.error_message == "Invalid API key."


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
        sampling=SamplingConfig(
            temperature=1.0,
            top_p=0.95,
            max_tokens=128,
        ),
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
