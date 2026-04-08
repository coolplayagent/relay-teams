# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NamedTuple, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_GRAPHQL_TIMEOUT_SECONDS = 30.0
_GITHUB_GRAPHQL_QUERY = """
query PullRequestLinkedIssues($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 1) {
        totalCount
      }
    }
  }
}
""".strip()


class PullRequestIssueLinkContext(NamedTuple):
    owner: str
    repository_name: str
    pull_request_number: int
    base_ref: str


class LinkedIssueCountFetcher(Protocol):
    def __call__(
        self,
        *,
        context: PullRequestIssueLinkContext,
        token: str,
        graphql_url: str,
    ) -> int: ...


class IssueLinkRequirementError(ValueError):
    pass


def build_graphql_url(api_url: str) -> str:
    normalized = api_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("GitHub API URL must not be empty")
    if normalized.endswith("/api/v3"):
        return normalized[: -len("/api/v3")] + "/api/graphql"
    if normalized.endswith("/api"):
        return normalized + "/graphql"
    return normalized + "/graphql"


def load_pull_request_issue_link_context(
    event_path: Path,
) -> PullRequestIssueLinkContext:
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    repository = _require_mapping(payload, "repository")
    owner = _require_mapping(repository, "owner")
    pull_request = _require_mapping(payload, "pull_request")
    base = _require_mapping(pull_request, "base")
    return PullRequestIssueLinkContext(
        owner=_require_text(owner, "login"),
        repository_name=_require_text(repository, "name"),
        pull_request_number=_require_int(pull_request, "number"),
        base_ref=_require_text(base, "ref"),
    )


def fetch_linked_issue_count(
    *,
    context: PullRequestIssueLinkContext,
    token: str,
    graphql_url: str,
) -> int:
    request_payload = json.dumps(
        {
            "query": _GITHUB_GRAPHQL_QUERY,
            "variables": {
                "owner": context.owner,
                "name": context.repository_name,
                "number": context.pull_request_number,
            },
        }
    ).encode("utf-8")
    request = Request(
        graphql_url,
        data=request_payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_GRAPHQL_TIMEOUT_SECONDS) as response:
            response_text = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise IssueLinkRequirementError(
            f"GitHub GraphQL request failed with HTTP {exc.code}: "
            f"{detail or exc.reason}"
        ) from exc
    except URLError as exc:
        raise IssueLinkRequirementError(
            f"Failed to reach GitHub GraphQL endpoint: {exc.reason}"
        ) from exc

    payload = json.loads(response_text)
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        detail = "; ".join(_error_message_text(error) for error in errors)
        raise IssueLinkRequirementError(f"GitHub GraphQL returned errors: {detail}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise IssueLinkRequirementError("GitHub GraphQL response did not include data")
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise IssueLinkRequirementError(
            f"Pull request #{context.pull_request_number} was not found in "
            f"{context.owner}/{context.repository_name}"
        )
    pull_request = repository.get("pullRequest")
    if not isinstance(pull_request, dict):
        raise IssueLinkRequirementError(
            f"Pull request #{context.pull_request_number} was not found in "
            f"{context.owner}/{context.repository_name}"
        )
    closing_issues_references = pull_request.get("closingIssuesReferences")
    if not isinstance(closing_issues_references, dict):
        raise IssueLinkRequirementError(
            "GitHub GraphQL response did not include closingIssuesReferences"
        )
    total_count = closing_issues_references.get("totalCount")
    if not isinstance(total_count, int):
        raise IssueLinkRequirementError(
            "GitHub GraphQL response did not include a valid linked issue count"
        )
    return total_count


def ensure_pull_request_links_issue(
    *,
    context: PullRequestIssueLinkContext,
    token: str,
    api_url: str,
    linked_issue_count_fetcher: LinkedIssueCountFetcher = fetch_linked_issue_count,
) -> int:
    linked_issue_count = linked_issue_count_fetcher(
        context=context,
        token=token,
        graphql_url=build_graphql_url(api_url),
    )
    if linked_issue_count > 0:
        return linked_issue_count
    raise IssueLinkRequirementError(
        f"Pull request #{context.pull_request_number} targeting {context.base_ref} "
        "must link at least one issue before it can be merged into main. "
        "Add a closing keyword such as 'Fixes #123' to the PR description or link "
        "an issue from the Development sidebar."
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise IssueLinkRequirementError(
            f"Required environment variable is missing: {name}"
        )
    return value


def _resolve_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token is None or not token.strip():
        raise IssueLinkRequirementError(
            "GITHUB_TOKEN or GH_TOKEN is required to validate linked issues"
        )
    return token.strip()


def _require_mapping(payload: object, key: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise IssueLinkRequirementError("GitHub event payload must be a JSON object")
    value = payload.get(key)
    if not isinstance(value, dict):
        raise IssueLinkRequirementError(
            f"GitHub event payload is missing object field: {key}"
        )
    return value


def _require_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IssueLinkRequirementError(
            f"GitHub event payload is missing text field: {key}"
        )
    return value


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise IssueLinkRequirementError(
            f"GitHub event payload is missing integer field: {key}"
        )
    return value


def _error_message_text(payload: object) -> str:
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return "unknown GraphQL error"


def main() -> int:
    try:
        event_path = Path(_required_env("GITHUB_EVENT_PATH"))
        context = load_pull_request_issue_link_context(event_path)
        if context.base_ref != "main":
            print(
                f"Skipping linked-issue check for PR #{context.pull_request_number}: "
                f"base branch is {context.base_ref}."
            )
            return 0
        linked_issue_count = ensure_pull_request_links_issue(
            context=context,
            token=_resolve_github_token(),
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        )
    except (
        IssueLinkRequirementError,
        OSError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"Validated PR #{context.pull_request_number}: "
        f"{linked_issue_count} linked issue(s) found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
