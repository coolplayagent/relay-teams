# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType

import httpx
import pytest

from relay_teams.release.pull_request_issue_link import (
    IssueLinkRequirementError,
    PullRequestIssueLinkContext,
    build_graphql_url,
    ensure_pull_request_links_issue,
    load_pull_request_issue_link_context,
)


class _FakeGitHubGraphqlClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        error: httpx.RequestError | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.posts: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def __aenter__(self) -> _FakeGitHubGraphqlClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> httpx.Response:
        self.posts.append((url, headers, json))
        if self._error is not None:
            raise self._error
        if self._response is None:
            raise RuntimeError("missing fake response")
        return self._response


def test_build_graphql_url_handles_github_dot_com() -> None:
    assert (
        build_graphql_url("https://api.github.com") == "https://api.github.com/graphql"
    )


def test_build_graphql_url_handles_github_enterprise_api_v3() -> None:
    assert (
        build_graphql_url("https://github.example.com/api/v3")
        == "https://github.example.com/api/graphql"
    )


def test_load_pull_request_issue_link_context_reads_event_payload(
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "pull_request_event.json"
    event_path.write_text(
        json.dumps(
            {
                "repository": {
                    "name": "agent-teams",
                    "owner": {"login": "openai"},
                },
                "pull_request": {
                    "number": 42,
                    "base": {"ref": "main"},
                },
            }
        ),
        encoding="utf-8",
    )

    context = load_pull_request_issue_link_context(event_path)

    assert context == PullRequestIssueLinkContext(
        owner="openai",
        repository_name="agent-teams",
        pull_request_number=42,
        base_ref="main",
    )


def test_ensure_pull_request_links_issue_accepts_positive_issue_count() -> None:
    context = PullRequestIssueLinkContext(
        owner="openai",
        repository_name="agent-teams",
        pull_request_number=42,
        base_ref="main",
    )

    linked_issue_count = ensure_pull_request_links_issue(
        context=context,
        token="ghp_secret",
        api_url="https://api.github.com",
        linked_issue_count_fetcher=lambda **_: 2,
    )

    assert linked_issue_count == 2


def test_ensure_pull_request_links_issue_rejects_missing_link() -> None:
    context = PullRequestIssueLinkContext(
        owner="openai",
        repository_name="agent-teams",
        pull_request_number=42,
        base_ref="main",
    )

    with pytest.raises(IssueLinkRequirementError, match="must link at least one issue"):
        _ = ensure_pull_request_links_issue(
            context=context,
            token="ghp_secret",
            api_url="https://api.github.com",
            linked_issue_count_fetcher=lambda **_: 0,
        )


def test_fetch_linked_issue_count_returns_count(monkeypatch) -> None:
    from relay_teams.release.pull_request_issue_link import (
        PullRequestIssueLinkContext,
        fetch_linked_issue_count,
    )

    context = PullRequestIssueLinkContext(
        owner="openai",
        repository_name="agent-teams",
        pull_request_number=42,
        base_ref="main",
    )

    response = httpx.Response(
        200,
        text=json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {"closingIssuesReferences": {"totalCount": 3}}
                    }
                }
            }
        ),
        request=httpx.Request("POST", "https://api.github.com/graphql"),
    )
    fake_client = _FakeGitHubGraphqlClient(response=response)
    captured_client_kwargs: dict[str, object] = {}

    def fake_create_runtime_async_http_client(
        **kwargs: object,
    ) -> _FakeGitHubGraphqlClient:
        captured_client_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        "relay_teams.release.pull_request_issue_link.create_runtime_async_http_client",
        fake_create_runtime_async_http_client,
    )

    count = fetch_linked_issue_count(
        context=context,
        token="ghp_secret",
        graphql_url="https://api.github.com/graphql",
    )

    assert count == 3
    assert fake_client.posts[0][0] == "https://api.github.com/graphql"
    assert fake_client.posts[0][1]["Authorization"] == "Bearer ghp_secret"
    assert captured_client_kwargs["ssl_verify"] is True


def test_fetch_linked_issue_count_raises_on_http_error(monkeypatch) -> None:
    from relay_teams.release.pull_request_issue_link import (
        PullRequestIssueLinkContext,
        fetch_linked_issue_count,
    )

    context = PullRequestIssueLinkContext(
        owner="openai",
        repository_name="agent-teams",
        pull_request_number=42,
        base_ref="main",
    )

    fake_client = _FakeGitHubGraphqlClient(
        response=httpx.Response(
            401,
            text="Unauthorized",
            request=httpx.Request("POST", "https://api.github.com/graphql"),
        )
    )
    monkeypatch.setattr(
        "relay_teams.release.pull_request_issue_link.create_runtime_async_http_client",
        lambda **_kwargs: fake_client,
    )

    with pytest.raises(IssueLinkRequirementError, match="HTTP 401"):
        fetch_linked_issue_count(
            context=context,
            token="ghp_secret",
            graphql_url="https://api.github.com/graphql",
        )


def test_fetch_linked_issue_count_raises_on_request_error(monkeypatch) -> None:
    from relay_teams.release.pull_request_issue_link import (
        PullRequestIssueLinkContext,
        fetch_linked_issue_count,
    )

    context = PullRequestIssueLinkContext(
        owner="openai",
        repository_name="agent-teams",
        pull_request_number=42,
        base_ref="main",
    )

    fake_client = _FakeGitHubGraphqlClient(
        error=httpx.ConnectError(
            "Connection refused",
            request=httpx.Request("POST", "https://api.github.com/graphql"),
        )
    )
    monkeypatch.setattr(
        "relay_teams.release.pull_request_issue_link.create_runtime_async_http_client",
        lambda **_kwargs: fake_client,
    )

    with pytest.raises(IssueLinkRequirementError, match="Failed to reach"):
        fetch_linked_issue_count(
            context=context,
            token="ghp_secret",
            graphql_url="https://api.github.com/graphql",
        )
