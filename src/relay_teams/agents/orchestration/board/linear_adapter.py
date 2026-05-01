# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from datetime import datetime

from relay_teams.agents.orchestration.board.adapter import (
    BoardTask,
    BoardTaskState,
    TaskBoardAdapter,
)
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

_LINEAR_STATE_MAP: dict[str, BoardTaskState] = {
    "unstarted": BoardTaskState.READY,
    "started": BoardTaskState.IN_PROGRESS,
    "completed": BoardTaskState.COMPLETED,
    "canceled": BoardTaskState.CANCELLED,
    "backlog": BoardTaskState.BACKLOG,
    "triage": BoardTaskState.BACKLOG,
    "in review": BoardTaskState.IN_REVIEW,
    "done": BoardTaskState.COMPLETED,
    "todo": BoardTaskState.READY,
    "in progress": BoardTaskState.IN_PROGRESS,
}


def _linear_issue_to_board(issue: Mapping[str, object]) -> BoardTask:
    state_raw = issue.get("state")
    state_dict = state_raw if isinstance(state_raw, dict) else {}
    state_name = str(state_dict.get("name", "backlog")).lower()
    board_state = _LINEAR_STATE_MAP.get(state_name, BoardTaskState.BACKLOG)

    assignee_info = issue.get("assignee")
    assignee: str | None = None
    if isinstance(assignee_info, dict):
        assignee = str(assignee_info.get("name", assignee_info.get("id", "")))

    labels_raw = issue.get("labels")
    labels: tuple[str, ...] = ()
    if isinstance(labels_raw, (list, tuple)):
        labels = tuple(
            str(lbl.get("name", lbl)) for lbl in labels_raw if isinstance(lbl, dict)
        )

    def _parse_dt(value: object) -> datetime | None:
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None

    return BoardTask(
        board_task_id=str(issue.get("id", "")),
        title=str(issue.get("title", "")),
        description=str(issue.get("description", "") or ""),
        state=board_state,
        assignee=assignee,
        labels=labels,
        source_url=str(issue.get("url", "")),
        created_at=_parse_dt(issue.get("createdAt")),
        updated_at=_parse_dt(issue.get("updatedAt")),
        raw_payload={k: v for k, v in issue.items()},
    )


class LinearAdapter(TaskBoardAdapter):
    """Linear adapter for the task board.

    Uses the Linear GraphQL API with API key auth.

    Configuration requirements:
      - linear_api_key_env: environment variable name holding the API key
      - linear_team_id: Linear team ID
    """

    def __init__(
        self,
        api_key: str,
        team_id: str,
    ) -> None:
        self._api_key = api_key
        self._team_id = team_id
        self._url = "https://api.linear.app/graphql"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }

    async def list_tasks(self, *, board_id: str) -> tuple[BoardTask, ...]:
        query = """
        query($teamId: String!) {
            team(id: $teamId) {
                issues { nodes { id title description state { name }
                    assignee { name id } labels { nodes { name } }
                    url createdAt updatedAt } }
            }
        }
        """
        payload = json.dumps(
            {"query": query, "variables": {"teamId": board_id}}
        ).encode()
        req = urllib.request.Request(
            self._url, data=payload, headers=self._headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except (OSError, ValueError) as exc:
            LOGGER.warning("failed to list Linear issues: %s", exc)
            return ()
        nodes = data.get("data", {}).get("team", {}).get("issues", {}).get("nodes", [])
        if not isinstance(nodes, list):
            return ()
        return tuple(_linear_issue_to_board(n) for n in nodes if isinstance(n, dict))

    async def get_task(self, *, task_id: str) -> BoardTask:
        query = """
        query($id: String!) {
            issue(id: $id) {
                id title description state { name }
                assignee { name id } labels { nodes { name } }
                url createdAt updatedAt
            }
        }
        """
        payload = json.dumps({"query": query, "variables": {"id": task_id}}).encode()
        req = urllib.request.Request(
            self._url, data=payload, headers=self._headers, method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        issue = data.get("data", {}).get("issue", {})
        return _linear_issue_to_board(issue)

    async def move_task(self, *, task_id: str, to_state: BoardTaskState) -> None:
        state_name = {
            BoardTaskState.BACKLOG: "Backlog",
            BoardTaskState.READY: "Todo",
            BoardTaskState.IN_PROGRESS: "In Progress",
            BoardTaskState.IN_REVIEW: "In Review",
            BoardTaskState.BLOCKED: "Todo",
            BoardTaskState.COMPLETED: "Done",
            BoardTaskState.CANCELLED: "Cancelled",
        }.get(to_state, "Todo")
        mutation = """
        mutation($id: String!, $stateName: String!) {
            issueUpdate(id: $id, input: { state: { name: $stateName } }) {
                success
            }
        }
        """
        payload = json.dumps(
            {
                "query": mutation,
                "variables": {"id": task_id, "stateName": state_name},
            }
        ).encode()
        req = urllib.request.Request(
            self._url, data=payload, headers=self._headers, method="POST"
        )
        with urllib.request.urlopen(req):
            pass

    async def assign_task(self, *, task_id: str, assignee: str) -> None:
        mutation = """
        mutation($id: String!, $assignee: String!) {
            issueUpdate(id: $id, input: { assignee: { name: $assignee } }) {
                success
            }
        }
        """
        payload = json.dumps(
            {
                "query": mutation,
                "variables": {"id": task_id, "assignee": assignee},
            }
        ).encode()
        req = urllib.request.Request(
            self._url, data=payload, headers=self._headers, method="POST"
        )
        with urllib.request.urlopen(req):
            pass

    async def add_comment(self, *, task_id: str, body: str) -> None:
        mutation = """
        mutation($id: String!, $body: String!) {
            commentCreate(input: { issueId: $id, body: $body }) {
                success
            }
        }
        """
        payload = json.dumps(
            {"query": mutation, "variables": {"id": task_id, "body": body}}
        ).encode()
        req = urllib.request.Request(
            self._url, data=payload, headers=self._headers, method="POST"
        )
        with urllib.request.urlopen(req):
            pass

    async def add_artifact(self, *, task_id: str, name: str, url: str) -> None:
        body = f"**{name}**: {url}"
        await self.add_comment(task_id=task_id, body=body)
