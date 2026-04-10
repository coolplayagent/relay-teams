# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping

import httpx

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.triggers.github_client import GitHubApiClient
import relay_teams.triggers.github_client as github_client_module


def test_list_pull_request_files_paginates(monkeypatch) -> None:
    requests: list[tuple[str, Mapping[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {str(key): str(value) for key, value in request.url.params.items()}
        requests.append((str(request.url), params))
        page = params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json=[{"filename": f"src/file_{index}.py"} for index in range(100)],
            )
        if page == "2":
            return httpx.Response(
                200,
                json=[{"filename": "src/final.py"}, {"filename": "README.md"}],
            )
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        github_client_module,
        "create_sync_http_client",
        lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client = GitHubApiClient(get_proxy_config=lambda: ProxyEnvConfig())

    filenames = client.list_pull_request_files(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        pull_request_number=318,
    )

    assert len(filenames) == 102
    assert filenames[-2:] == ("src/final.py", "README.md")
    assert [params["page"] for _, params in requests] == ["1", "2"]
    assert all(params["per_page"] == "100" for _, params in requests)
