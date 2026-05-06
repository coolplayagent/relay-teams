# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime

import httpx
from pydantic import JsonValue

from relay_teams.boards.adapter import (
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
)
from relay_teams.logger import get_logger
from relay_teams.net.clients import create_async_http_client

LOGGER = get_logger(__name__)

_HTTP_TIMEOUT_SECONDS = 30.0

_GITHUB_STATE_MAP: dict[str, BoardTaskState] = {
    "open": BoardTaskState.READY,
    "in_progress": BoardTaskState.IN_PROGRESS,
    "closed": BoardTaskState.COMPLETED,
}


def _github_issue_to_board(issue: dict[str, JsonValue]) -> BoardTask:
    state_str = str(issue.get("state", "open"))
    board_state = _GITHUB_STATE_MAP.get(state_str, BoardTaskState.BACKLOG)
    labels_raw = issue.get("labels")
    labels: tuple[str, ...] = ()
    if isinstance(labels_raw, (list, tuple)):
        labels = tuple(
            str(lbl.get("name", str(lbl)))
            for lbl in labels_raw
            if isinstance(lbl, dict)
        )
    created_raw = issue.get("created_at")
    updated_raw = issue.get("updated_at")

    def _parse_dt(value: object) -> datetime | None:
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None

    assignee_raw = issue.get("assignee")
    assignee: str | None = None
    if isinstance(assignee_raw, dict):
        assignee = str(assignee_raw.get("login", ""))

    return BoardTask(
        board_task_id=str(issue.get("number", "")),
        title=str(issue.get("title", "")),
        description=str(issue.get("body", "") or ""),
        state=board_state,
        assignee=assignee,
        labels=labels,
        source_url=str(issue.get("html_url", "")),
        created_at=_parse_dt(created_raw),
        updated_at=_parse_dt(updated_raw),
        raw_payload=dict(issue),
    )


def _board_state_to_github(state: BoardTaskState) -> str:
    mapping: dict[BoardTaskState, str] = {
        BoardTaskState.BACKLOG: "open",
        BoardTaskState.READY: "open",
        BoardTaskState.IN_PROGRESS: "open",
        BoardTaskState.IN_REVIEW: "open",
        BoardTaskState.BLOCKED: "open",
        BoardTaskState.COMPLETED: "closed",
        BoardTaskState.CANCELLED: "closed",
    }
    return mapping.get(state, "open")


class GitHubAdapter(TaskBoardAdapter):
    """GitHub Issues adapter for the task board.

    Uses the GitHub REST API (v3) with personal access token auth.

    Configuration requirements:
      - github_repo: "owner/repo" format
      - github_token_env: environment variable name holding the token
    """

    def __init__(
        self,
        github_repo: str,
        github_token: str,
    ) -> None:
        self._repo = github_repo
        self._token = github_token
        self._base_url = "https://api.github.com"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
        url = f"{self._base_url}/repos/{self._repo}/issues"
        try:
            async with create_async_http_client(
                timeout_seconds=_HTTP_TIMEOUT_SECONDS,
            ) as client:
                response = await client.get(url, headers=self._headers)
                data = response.json()
        except (
            httpx.HTTPStatusError,
            httpx.TransportError,
            json.JSONDecodeError,
        ) as exc:
            LOGGER.warning("failed to list GitHub issues: %s", exc)
            return ()
        if not isinstance(data, list):
            return ()
        return tuple(
            _github_issue_to_board(issue)
            for issue in data
            if isinstance(issue, dict) and "pull_request" not in issue
        )

    async def get_task(self, *, task_id: str) -> BoardTask:
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}"
        async with create_async_http_client(
            timeout_seconds=_HTTP_TIMEOUT_SECONDS,
        ) as client:
            response = await client.get(url, headers=self._headers)
            response.raise_for_status()
            data = response.json()
        return _github_issue_to_board(data)

    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        github_state = _board_state_to_github(to_state)
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}"
        payload = json.dumps({"state": github_state}).encode()
        async with create_async_http_client(
            timeout_seconds=_HTTP_TIMEOUT_SECONDS,
        ) as client:
            response = await client.patch(
                url,
                content=payload,
                headers={**self._headers, "Content-Type": "application/json"},
            )
            response.raise_for_status()

    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}/assignees"
        payload = json.dumps({"assignees": [assignee]}).encode()
        async with create_async_http_client(
            timeout_seconds=_HTTP_TIMEOUT_SECONDS,
        ) as client:
            response = await client.post(
                url,
                content=payload,
                headers={**self._headers, "Content-Type": "application/json"},
            )
            response.raise_for_status()

    async def add_comment(self, *, task_id: str, body: str) -> None:
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}/comments"
        payload = json.dumps({"body": body}).encode()
        async with create_async_http_client(
            timeout_seconds=_HTTP_TIMEOUT_SECONDS,
        ) as client:
            response = await client.post(
                url,
                content=payload,
                headers={**self._headers, "Content-Type": "application/json"},
            )
            response.raise_for_status()

    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        body = f"**{name}**: {url}"
        await self.add_comment(task_id=task_id, body=body)
