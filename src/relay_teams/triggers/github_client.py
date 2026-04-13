# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Mapping
from urllib.parse import quote

import httpx
from pydantic import JsonValue

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.net.clients import create_sync_http_client

_GITHUB_API_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT_SECONDS = 20.0
_API_VERSION = "2022-11-28"
_PULL_REQUEST_FILES_PAGE_SIZE = 100
_REPOSITORY_LIST_PAGE_SIZE = 100
_REPOSITORY_LIST_MAX_PAGES = 10


class GitHubApiError(RuntimeError):
    def __init__(
        self,
        *,
        message: str,
        status_code: int | None = None,
        response_payload: JsonValue | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_payload = response_payload


JsonObject = dict[str, JsonValue]
JsonArray = list[JsonValue]


class GitHubApiClient:
    def __init__(
        self,
        *,
        get_proxy_config: Callable[[], ProxyEnvConfig],
        base_url: str = _GITHUB_API_BASE_URL,
    ) -> None:
        self._get_proxy_config = get_proxy_config
        self._base_url = base_url.rstrip("/")

    def get_repository(self, *, token: str, owner: str, repo: str) -> JsonObject:
        return self._request_object_json(
            token=token,
            method="GET",
            path=f"/repos/{owner}/{repo}",
        )

    def list_repositories(
        self,
        *,
        token: str,
        query: str | None = None,
    ) -> tuple[JsonObject, ...]:
        normalized_query = _normalize_query(query)
        repositories: list[JsonObject] = []
        seen_full_names: set[str] = set()
        for page in range(1, _REPOSITORY_LIST_MAX_PAGES + 1):
            payload = self._request_json(
                token=token,
                method="GET",
                path="/user/repos",
                query_params={
                    "sort": "updated",
                    "direction": "desc",
                    "affiliation": "owner,collaborator,organization_member",
                    "per_page": str(_REPOSITORY_LIST_PAGE_SIZE),
                    "page": str(page),
                },
            )
            if not isinstance(payload, list):
                raise GitHubApiError(message="Unexpected repositories response")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                full_name = _repository_full_name(item)
                if full_name is None:
                    continue
                if (
                    normalized_query is not None
                    and normalized_query not in full_name.lower()
                ):
                    continue
                if full_name in seen_full_names:
                    continue
                seen_full_names.add(full_name)
                repositories.append(item)
            if len(payload) < _REPOSITORY_LIST_PAGE_SIZE:
                break
        return tuple(repositories)

    def register_repository_webhook(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        callback_url: str,
        webhook_secret: str,
        events: tuple[str, ...],
    ) -> JsonObject:
        return self._request_object_json(
            token=token,
            method="POST",
            path=f"/repos/{owner}/{repo}/hooks",
            json_body={
                "name": "web",
                "active": True,
                "events": list(events),
                "config": {
                    "url": callback_url,
                    "content_type": "json",
                    "insecure_ssl": "0",
                    "secret": webhook_secret,
                },
            },
        )

    def delete_repository_webhook(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        webhook_id: str,
    ) -> None:
        _ = self._request_json(
            token=token,
            method="DELETE",
            path=f"/repos/{owner}/{repo}/hooks/{webhook_id}",
            allow_empty_response=True,
        )

    def list_pull_request_files(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        pull_request_number: int,
    ) -> tuple[str, ...]:
        filenames: list[str] = []
        page = 1
        while True:
            response = self._request_json(
                token=token,
                method="GET",
                path=f"/repos/{owner}/{repo}/pulls/{pull_request_number}/files",
                query_params={
                    "per_page": str(_PULL_REQUEST_FILES_PAGE_SIZE),
                    "page": str(page),
                },
            )
            if not isinstance(response, list):
                raise GitHubApiError(message="Unexpected pull request files response")
            for item in response:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                if isinstance(filename, str) and filename.strip():
                    filenames.append(filename.strip())
            if len(response) < _PULL_REQUEST_FILES_PAGE_SIZE:
                break
            page += 1
        return tuple(filenames)

    def create_issue_comment(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> JsonObject:
        return self._request_object_json(
            token=token,
            method="POST",
            path=f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json_body={"body": body},
        )

    def add_labels(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
        labels: tuple[str, ...],
    ) -> JsonObject:
        return self._request_object_json(
            token=token,
            method="POST",
            path=f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json_body={"labels": list(labels)},
        )

    def remove_label(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
    ) -> None:
        _ = self._request_json(
            token=token,
            method="DELETE",
            path=f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{quote(label, safe='')}",
            allow_empty_response=True,
        )

    def add_assignees(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
        assignees: tuple[str, ...],
    ) -> JsonObject:
        return self._request_object_json(
            token=token,
            method="POST",
            path=f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
            json_body={"assignees": list(assignees)},
        )

    def remove_assignees(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        issue_number: int,
        assignees: tuple[str, ...],
    ) -> JsonObject:
        return self._request_object_json(
            token=token,
            method="DELETE",
            path=f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
            json_body={"assignees": list(assignees)},
        )

    def set_commit_status(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        sha: str,
        state: str,
        context: str,
        description: str | None = None,
        target_url: str | None = None,
    ) -> JsonObject:
        payload: JsonObject = {
            "state": state,
            "context": context,
        }
        if description is not None:
            payload["description"] = description
        if target_url is not None:
            payload["target_url"] = target_url
        return self._request_object_json(
            token=token,
            method="POST",
            path=f"/repos/{owner}/{repo}/statuses/{sha}",
            json_body=payload,
        )

    def _request_object_json(
        self,
        *,
        token: str,
        method: str,
        path: str,
        json_body: JsonObject | None = None,
        query_params: Mapping[str, str] | None = None,
        allow_empty_response: bool = False,
    ) -> JsonObject:
        payload = self._request_json(
            token=token,
            method=method,
            path=path,
            json_body=json_body,
            query_params=query_params,
            allow_empty_response=allow_empty_response,
        )
        if isinstance(payload, dict):
            return payload
        raise GitHubApiError(message="Unexpected GitHub API response payload shape")

    def _request_json(
        self,
        *,
        token: str,
        method: str,
        path: str,
        json_body: JsonObject | None = None,
        query_params: Mapping[str, str] | None = None,
        allow_empty_response: bool = False,
    ) -> JsonObject | JsonArray:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        with create_sync_http_client(
            proxy_config=self._get_proxy_config(),
            follow_redirects=True,
            timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
        ) as client:
            try:
                response = client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=query_params,
                )
            except httpx.HTTPError as exc:
                raise GitHubApiError(message=str(exc)) from exc
        if response.status_code >= 400:
            raise GitHubApiError(
                message=_extract_error_message(response),
                status_code=response.status_code,
                response_payload=_parse_response_payload(response),
            )
        if allow_empty_response and not response.content:
            return {}
        payload = _parse_response_payload(response)
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return payload
        if allow_empty_response:
            return {}
        raise GitHubApiError(
            message="Unexpected GitHub API response",
            status_code=response.status_code,
        )


def _parse_response_payload(
    response: httpx.Response,
) -> JsonObject | JsonArray:
    if not response.content:
        return {}
    try:
        parsed = response.json()
    except ValueError:
        return {"raw": response.text}
    if isinstance(parsed, dict):
        return _normalize_json_mapping(parsed)
    if isinstance(parsed, list):
        return _normalize_json_list(parsed)
    return {"raw": response.text}


def _normalize_json_mapping(value: Mapping[str, object]) -> JsonObject:
    normalized: JsonObject = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            normalized[str(key)] = item
            continue
        if isinstance(item, dict):
            normalized[str(key)] = _normalize_json_mapping(item)
            continue
        if isinstance(item, list):
            normalized[str(key)] = _normalize_json_list(item)
            continue
        normalized[str(key)] = str(item)
    return normalized


def _normalize_json_list(value: list[object]) -> JsonArray:
    normalized: JsonArray = []
    for item in value:
        if isinstance(item, (str, int, float, bool)) or item is None:
            normalized.append(item)
            continue
        if isinstance(item, dict):
            normalized.append(_normalize_json_mapping(item))
            continue
        if isinstance(item, list):
            normalized.append(_normalize_json_list(item))
            continue
        normalized.append(str(item))
    return normalized


def _extract_error_message(response: httpx.Response) -> str:
    payload = _parse_response_payload(response)
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return f"GitHub API request failed with status {response.status_code}"


def _normalize_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return normalized


def _repository_full_name(payload: JsonObject) -> str | None:
    full_name = payload.get("full_name")
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    owner_payload = payload.get("owner")
    owner = None
    if isinstance(owner_payload, dict):
        login = owner_payload.get("login")
        if isinstance(login, str) and login.strip():
            owner = login.strip()
    name = payload.get("name")
    if owner is None or not isinstance(name, str) or not name.strip():
        return None
    return f"{owner}/{name.strip()}"


__all__ = [
    "GitHubApiClient",
    "GitHubApiError",
]
