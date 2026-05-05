# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from relay_teams.release.pull_request_issue_link import (
    IssueLinkRequirementError,
    PullRequestIssueLinkContext,
    build_graphql_url,
    ensure_pull_request_links_issue,
    load_pull_request_issue_link_context,
)


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


def test_fetch_linked_issue_count_returns_count() -> None:
    from unittest.mock import MagicMock, patch

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

    with patch(
        "relay_teams.release.pull_request_issue_link.create_sync_http_client"
    ) as mock_factory:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {"closingIssuesReferences": {"totalCount": 3}}
                    }
                }
            }
        )
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_factory.return_value = mock_client
        count = fetch_linked_issue_count(
            context=context,
            token="ghp_secret",
            graphql_url="https://api.github.com/graphql",
        )
        assert count == 3
