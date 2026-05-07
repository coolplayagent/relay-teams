# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import json

import httpx
import pytest

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.triggers.github_client import GitHubApiClient
import relay_teams.triggers.github_client as github_client_module


@pytest.mark.asyncio
async def test_list_pull_request_files_paginates(monkeypatch) -> None:
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
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    filenames = await client.list_pull_request_files(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        pull_request_number=318,
    )

    assert len(filenames) == 102
    assert filenames[-2:] == ("src/final.py", "README.md")
    assert [params["page"] for _, params in requests] == ["1", "2"]
    assert all(params["per_page"] == "100" for _, params in requests)


@pytest.mark.asyncio
async def test_repository_and_issue_methods_use_async_http_client(monkeypatch) -> None:
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload: object = None
        if request.content:
            payload = json.loads(request.content.decode("utf-8"))
        requests.append((request.method, request.url.path, payload))
        if request.method == "DELETE" and request.url.path.endswith("/hooks/42"):
            return httpx.Response(204)
        if request.method == "DELETE" and "/labels/" in request.url.path:
            return httpx.Response(204)
        if request.url.path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "full_name": "coolplayagent/relay-teams",
                        "owner": {"login": "coolplayagent"},
                    }
                ],
            )
        return httpx.Response(200, json={"id": "ok"})

    monkeypatch.setattr(
        github_client_module,
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    repository = await client.get_repository(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
    )
    repositories = await client.list_repositories(token="ghp_test", query="relay")
    webhook = await client.register_repository_webhook(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        callback_url="https://example.test/webhook",
        webhook_secret="secret",
        events=("pull_request",),
    )
    await client.delete_repository_webhook(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        webhook_id="42",
    )
    comment = await client.create_issue_comment(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        issue_number=707,
        body="done",
    )
    labels = await client.add_labels(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        issue_number=707,
        labels=("async",),
    )
    await client.remove_label(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        issue_number=707,
        label="needs review",
    )
    assignees = await client.add_assignees(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        issue_number=707,
        assignees=("stevetdp",),
    )
    removed_assignees = await client.remove_assignees(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        issue_number=707,
        assignees=("stevetdp",),
    )
    status = await client.set_commit_status(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        sha="abc123",
        state="success",
        context="async-net",
        description="ok",
        target_url="https://example.test/status",
    )

    assert repository == {"id": "ok"}
    assert repositories[0]["full_name"] == "coolplayagent/relay-teams"
    assert webhook == {"id": "ok"}
    assert comment == {"id": "ok"}
    assert labels == {"id": "ok"}
    assert assignees == {"id": "ok"}
    assert removed_assignees == {"id": "ok"}
    assert status == {"id": "ok"}
    assert requests == [
        ("GET", "/repos/coolplayagent/relay-teams", None),
        ("GET", "/user/repos", None),
        (
            "POST",
            "/repos/coolplayagent/relay-teams/hooks",
            {
                "name": "web",
                "active": True,
                "events": ["pull_request"],
                "config": {
                    "url": "https://example.test/webhook",
                    "content_type": "json",
                    "insecure_ssl": "0",
                    "secret": "secret",
                },
            },
        ),
        ("DELETE", "/repos/coolplayagent/relay-teams/hooks/42", None),
        (
            "POST",
            "/repos/coolplayagent/relay-teams/issues/707/comments",
            {"body": "done"},
        ),
        (
            "POST",
            "/repos/coolplayagent/relay-teams/issues/707/labels",
            {"labels": ["async"]},
        ),
        (
            "DELETE",
            "/repos/coolplayagent/relay-teams/issues/707/labels/needs review",
            None,
        ),
        (
            "POST",
            "/repos/coolplayagent/relay-teams/issues/707/assignees",
            {"assignees": ["stevetdp"]},
        ),
        (
            "DELETE",
            "/repos/coolplayagent/relay-teams/issues/707/assignees",
            {"assignees": ["stevetdp"]},
        ),
        (
            "POST",
            "/repos/coolplayagent/relay-teams/statuses/abc123",
            {
                "state": "success",
                "context": "async-net",
                "description": "ok",
                "target_url": "https://example.test/status",
            },
        ),
    ]
