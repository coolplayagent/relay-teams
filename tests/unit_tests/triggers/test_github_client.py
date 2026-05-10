# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json

import httpx
import pytest

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.triggers.github_client import GitHubApiClient, GitHubApiError
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


@pytest.mark.asyncio
async def test_repository_pull_request_methods_use_async_http_client(
    monkeypatch,
) -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {str(key): str(value) for key, value in request.url.params.items()}
        requests.append((request.method, request.url.path, params))
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json={"number": 42, "merged": True})
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "updated_at": "2026-05-10T09:30:00Z"},
                    {"number": 2, "updated_at": "2026-05-10T08:00:00Z"},
                    {"number": 3, "updated_at": ""},
                    {"number": 4, "updated_at": "not-a-date"},
                    {"number": 5, "updated_at": "2026-05-10T10:00:00"},
                ],
            )
        return httpx.Response(404, json={"message": "not found"})

    monkeypatch.setattr(
        github_client_module,
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    pull_request = await client.get_repository_pull_request(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        pull_request_number=42,
    )
    pull_requests = await client.list_repository_pull_requests(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        updated_since=datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
    )

    assert pull_request["number"] == 42
    assert [item["number"] for item in pull_requests] == [1, 3, 4, 5]
    assert requests[0] == (
        "GET",
        "/repos/coolplayagent/relay-teams/pulls/42",
        {},
    )
    assert requests[1][0:2] == (
        "GET",
        "/repos/coolplayagent/relay-teams/pulls",
    )
    assert requests[1][2]["state"] == "all"
    assert requests[1][2]["sort"] == "updated"


@pytest.mark.asyncio
async def test_repository_issues_paginate_and_include_since(monkeypatch) -> None:
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {str(key): str(value) for key, value in request.url.params.items()}
        requests.append(params)
        if params["page"] == "1":
            return httpx.Response(
                200,
                json=[{"number": number} for number in range(1, 101)],
            )
        return httpx.Response(200, json=[{"number": 101}, "ignored"])

    monkeypatch.setattr(
        github_client_module,
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    issues = await client.list_repository_issues(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        state="open",
        updated_since=datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
    )

    assert len(issues) == 101
    assert [params["page"] for params in requests] == ["1", "2"]
    assert all(params["state"] == "open" for params in requests)
    assert all(params["sort"] == "updated" for params in requests)
    assert all("since" in params for params in requests)


@pytest.mark.asyncio
async def test_repository_pull_requests_paginate_until_older_update(
    monkeypatch,
) -> None:
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {str(key): str(value) for key, value in request.url.params.items()}
        requests.append(params)
        if params["page"] == "1":
            return httpx.Response(
                200,
                json=[
                    {"number": number, "updated_at": "2026-05-10T10:00:00Z"}
                    for number in range(1, 101)
                ],
            )
        return httpx.Response(
            200,
            json=[
                {"number": 101, "updated_at": "2026-05-10T08:00:00Z"},
                {"number": 102, "updated_at": "2026-05-10T10:30:00Z"},
            ],
        )

    monkeypatch.setattr(
        github_client_module,
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    pull_requests = await client.list_repository_pull_requests(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        updated_since=datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
    )

    assert len(pull_requests) == 101
    assert pull_requests[-1]["number"] == 102
    assert [params["page"] for params in requests] == ["1", "2"]


@pytest.mark.asyncio
async def test_issue_timeline_events_paginate(monkeypatch) -> None:
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {str(key): str(value) for key, value in request.url.params.items()}
        requests.append(params)
        if params["page"] == "1":
            return httpx.Response(
                200,
                json=[{"event": "cross-referenced"} for _index in range(100)],
            )
        return httpx.Response(200, json=[{"event": "connected"}, "ignored"])

    monkeypatch.setattr(
        github_client_module,
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    events = await client.list_issue_timeline_events(
        token="ghp_test",
        owner="coolplayagent",
        repo="relay-teams",
        issue_number=707,
    )

    assert len(events) == 101
    assert [params["page"] for params in requests] == ["1", "2"]
    assert all(params["per_page"] == "100" for params in requests)


@pytest.mark.asyncio
async def test_list_endpoints_reject_unexpected_payloads(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    monkeypatch.setattr(
        github_client_module,
        "create_async_http_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = GitHubApiClient(get_proxy_config=ProxyEnvConfig)

    with pytest.raises(GitHubApiError, match="Unexpected issues response"):
        await client.list_repository_issues(
            token="ghp_test",
            owner="coolplayagent",
            repo="relay-teams",
        )
    with pytest.raises(GitHubApiError, match="Unexpected pull requests response"):
        await client.list_repository_pull_requests(
            token="ghp_test",
            owner="coolplayagent",
            repo="relay-teams",
        )
    with pytest.raises(GitHubApiError, match="Unexpected issue timeline response"):
        await client.list_issue_timeline_events(
            token="ghp_test",
            owner="coolplayagent",
            repo="relay-teams",
            issue_number=707,
        )
