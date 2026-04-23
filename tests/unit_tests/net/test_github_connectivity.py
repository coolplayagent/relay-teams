# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx

from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.net.github_connectivity import (
    GitHubWebhookConnectivityProbeRequest,
    GitHubWebhookConnectivityProbeService,
)
from relay_teams.env.proxy_env import resolve_proxy_env_config


class _FakeProbeClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: list[str] = []

    def __enter__(self) -> _FakeProbeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        return self._response


def test_probe_github_webhook_marks_inactive_temporary_public_url(monkeypatch) -> None:
    health_url = "https://expired-tunnel.lhr.life/api/system/health"
    fake_client = _FakeProbeClient(
        httpx.Response(
            503,
            request=httpx.Request("GET", health_url),
            text="<h1>no tunnel here :(</h1>",
        )
    )
    monkeypatch.setattr(
        "relay_teams.net.github_connectivity.create_sync_http_client",
        lambda **_kwargs: fake_client,
    )
    service = GitHubWebhookConnectivityProbeService(
        get_github_config=lambda: GitHubConfig(token=None, webhook_base_url=None),
        get_proxy_config=lambda: resolve_proxy_env_config(
            {"HTTPS_PROXY": "http://proxy.example:8443"}
        ),
    )

    result = service.probe(
        GitHubWebhookConnectivityProbeRequest(
            webhook_base_url="https://expired-tunnel.lhr.life"
        )
    )

    assert fake_client.calls == [health_url]
    assert result.ok is False
    assert result.status_code == 503
    assert result.retryable is True
    assert result.error_code == "temporary_public_url_inactive"
    assert (
        result.error_message
        == "Temporary public URL is inactive. Create a new temporary URL and retry."
    )
    assert result.diagnostics.endpoint_reachable is False
    assert result.diagnostics.used_proxy is True
