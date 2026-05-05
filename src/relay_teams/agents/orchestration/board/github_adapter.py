# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.request
from datetime import datetime

from pydantic import JsonValue

from relay_teams.agents.orchestration.board.adapter import (
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
)
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

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
        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req) as resp:  # nosec B310 - HTTPS URL with user-controlled config
                data = json.loads(resp.read())
        except (OSError, ValueError) as exc:
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
        req = urllib.request.Request(url, headers=self._headers)
        with urllib.request.urlopen(req) as resp:  # nosec B310 - HTTPS URL with user-controlled config
            data = json.loads(resp.read())
        return _github_issue_to_board(data)

    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        github_state = _board_state_to_github(to_state)
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}"
        payload = json.dumps({"state": github_state}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(req):  # nosec B310 - HTTPS URL with user-controlled config
            pass

    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}/assignees"
        payload = json.dumps({"assignees": [assignee]}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req):  # nosec B310 - HTTPS URL with user-controlled config
            pass

    async def add_comment(self, *, task_id: str, body: str) -> None:
        url = f"{self._base_url}/repos/{self._repo}/issues/{task_id}/comments"
        payload = json.dumps({"body": body}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req):  # nosec B310 - HTTPS URL with user-controlled config
            pass

    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        body = f"**{name}**: {url}"
        await self.add_comment(task_id=task_id, body=body)
