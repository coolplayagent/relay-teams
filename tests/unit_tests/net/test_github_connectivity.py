# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

import httpx

from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.net.github_cli_errors import GitHubCliNotFoundError
from relay_teams.net.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeService,
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


def test_probe_github_connectivity_reports_missing_token() -> None:
    service = GitHubConnectivityProbeService(
        get_github_config=lambda: GitHubConfig(token=None, webhook_base_url=None),
        get_proxy_config=lambda: resolve_proxy_env_config({}),
    )

    result = service.probe(GitHubConnectivityProbeRequest())

    assert result.ok is False
    assert result.error_code == "missing_token"
    assert result.diagnostics.binary_available is False
    assert result.diagnostics.auth_valid is False


def test_probe_github_connectivity_reports_missing_gh_cli(monkeypatch) -> None:
    async def fake_get_gh_path() -> Path:
        raise GitHubCliNotFoundError()

    monkeypatch.setattr(
        "relay_teams.net.github_connectivity.get_gh_path",
        fake_get_gh_path,
    )
    service = GitHubConnectivityProbeService(
        get_github_config=lambda: GitHubConfig(
            token="ghp_configured", webhook_base_url=None
        ),
        get_proxy_config=lambda: resolve_proxy_env_config({}),
    )

    result = service.probe(GitHubConnectivityProbeRequest())

    assert result.ok is False
    assert result.error_code == "gh_unavailable"
    assert result.error_message is not None
    assert "GitHub CLI is not available" in result.error_message
    assert result.diagnostics.binary_available is False


def test_probe_github_connectivity_async_reports_missing_gh_cli(monkeypatch) -> None:
    async def fake_get_gh_path() -> Path:
        raise GitHubCliNotFoundError()

    monkeypatch.setattr(
        "relay_teams.net.github_connectivity.get_gh_path",
        fake_get_gh_path,
    )
    service = GitHubConnectivityProbeService(
        get_github_config=lambda: GitHubConfig(
            token="ghp_configured", webhook_base_url=None
        ),
        get_proxy_config=lambda: resolve_proxy_env_config({}),
    )

    result = asyncio.run(service.probe_async(GitHubConnectivityProbeRequest()))

    assert result.ok is False
    assert result.error_code == "gh_unavailable"
    assert result.error_message is not None
    assert "GitHub CLI is not available" in result.error_message
    assert result.diagnostics.binary_available is False


def test_probe_github_connectivity_async_uses_async_gh_resolution(monkeypatch) -> None:
    gh_path = Path("/usr/bin/gh")
    resolved_paths: list[str] = []
    commands: list[list[str]] = []

    async def fake_get_gh_path() -> Path:
        resolved_paths.append("async")
        return gh_path

    def fake_read_gh_version(
        path: Path,
        *,
        env: dict[str, str],
    ) -> str:
        assert path == gh_path
        assert env["GH_TOKEN"] == "ghp_secret"
        return "gh version 2.88.1"

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        encoding: str,
        errors: str,
        env: dict[str, str],
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert capture_output is True
        assert text is True
        assert encoding == "utf-8"
        assert errors == "replace"
        assert env["GH_TOKEN"] == "ghp_secret"
        assert timeout == 2.5
        assert check is False
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"login":"octocat"}',
            stderr="",
        )

    monkeypatch.setattr(
        "relay_teams.net.github_connectivity.get_gh_path",
        fake_get_gh_path,
    )
    monkeypatch.setattr(
        "relay_teams.net.github_connectivity._read_gh_version",
        fake_read_gh_version,
    )
    monkeypatch.setattr(
        "relay_teams.net.github_connectivity.subprocess.run",
        fake_run,
    )

    service = GitHubConnectivityProbeService(
        get_github_config=lambda: GitHubConfig(token=None, webhook_base_url=None),
        get_proxy_config=lambda: resolve_proxy_env_config({}),
    )

    result = asyncio.run(
        service.probe_async(
            GitHubConnectivityProbeRequest(token="ghp_secret", timeout_ms=2500)
        )
    )

    assert resolved_paths == ["async"]
    assert commands == [[str(gh_path), "api", "user"]]
    assert result.ok is True
    assert result.username == "octocat"
    assert result.gh_path == str(gh_path)
    assert result.gh_version == "gh version 2.88.1"


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
