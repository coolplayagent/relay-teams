from __future__ import annotations

import json
from collections.abc import Generator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict

from agent_teams.shared_types.json_types import JsonArray, JsonObject, JsonValue


class RunHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    session_id: str


class AgentTeamsClient:
    """HTTP client for the Agent Teams server API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout_seconds: float = 30.0,
        stream_timeout_seconds: float = 600.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._stream_timeout_seconds = stream_timeout_seconds

    def health(self) -> JsonObject:
        return self._request_json("GET", "/api/system/health")

    def create_session(
        self, session_id: str | None = None, metadata: dict[str, str] | None = None
    ) -> JsonObject:
        metadata_payload: JsonObject | None = None
        if metadata is not None:
            metadata_payload = {key: value for key, value in metadata.items()}
        payload: JsonObject = {"session_id": session_id, "metadata": metadata_payload}
        return self._request_json(
            "POST",
            "/api/sessions",
            payload,
        )

    def create_run(
        self,
        intent: str,
        session_id: str | None = None,
        execution_mode: str = "ai",
    ) -> RunHandle:
        payload: JsonObject = {
            "session_id": session_id,
            "intent": intent,
            "execution_mode": execution_mode,
        }
        data = self._request_json("POST", "/api/runs", payload)
        return RunHandle(
            run_id=_expect_str(data.get("run_id"), "run_id"),
            session_id=_expect_str(data.get("session_id"), "session_id"),
        )

    def stream_run_events(self, run_id: str) -> Generator[JsonObject, None, None]:
        url = f"{self._base_url}/api/runs/{run_id}/events"
        request = Request(
            url=url, method="GET", headers={"Accept": "text/event-stream"}
        )

        try:
            with urlopen(request, timeout=self._stream_timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    yield json.loads(payload)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"HTTP {exc.code} while streaming run events: {body}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Failed to connect to server: {exc}") from exc

    def list_tool_approvals(self, run_id: str) -> list[JsonObject]:
        data = self._request_json("GET", f"/api/runs/{run_id}/tool-approvals")
        items = data.get("data", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def resolve_tool_approval(
        self, run_id: str, tool_call_id: str, action: str, feedback: str = ""
    ) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve",
            {"action": action, "feedback": feedback},
        )

    def create_workflow(
        self,
        run_id: str,
        objective: str,
        workflow_id: str = "custom",
        tasks: list[JsonObject] | None = None,
    ) -> JsonObject:
        tasks_payload: list[JsonValue] | None = None
        if tasks is not None:
            tasks_payload = [task for task in tasks]
        payload: JsonObject = {
            "objective": objective,
            "workflow_id": workflow_id,
            "tasks": tasks_payload,
        }
        return self._request_json(
            "POST",
            f"/api/workflows/runs/{run_id}",
            payload,
        )

    def get_workflow_status(self, run_id: str, workflow_id: str) -> JsonObject:
        return self._request_json(
            "GET",
            f"/api/workflows/runs/{run_id}/{workflow_id}",
        )

    def dispatch_tasks(
        self,
        run_id: str,
        workflow_id: str,
        action: str,
        feedback: str = "",
        max_dispatch: int = 1,
    ) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/workflows/runs/{run_id}/{workflow_id}/dispatch",
            {"action": action, "feedback": feedback, "max_dispatch": max_dispatch},
        )

    def inject_message(self, run_id: str, content: str) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/inject",
            {"content": content},
        )

    def stop_run(self, run_id: str) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/stop",
            {"scope": "main"},
        )

    def resume_run(self, run_id: str) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}:resume",
            {},
        )

    def stop_subagent(self, run_id: str, instance_id: str) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/stop",
            {"scope": "subagent", "instance_id": instance_id},
        )

    def create_trigger(
        self,
        *,
        name: str,
        source_type: str,
        display_name: str | None = None,
        source_config: JsonObject | None = None,
        auth_policies: list[JsonObject] | None = None,
        target_config: JsonObject | None = None,
        public_token: str | None = None,
        enabled: bool = True,
    ) -> JsonObject:
        payload: JsonObject = {
            "name": name,
            "source_type": source_type,
            "enabled": enabled,
        }
        if display_name is not None:
            payload["display_name"] = display_name
        if source_config is not None:
            payload["source_config"] = source_config
        if auth_policies is not None:
            policies_payload: JsonArray = [policy for policy in auth_policies]
            payload["auth_policies"] = policies_payload
        if target_config is not None:
            payload["target_config"] = target_config
        if public_token is not None:
            payload["public_token"] = public_token
        return self._request_json("POST", "/api/triggers", payload)

    def list_triggers(self) -> list[JsonObject]:
        data = self._request_json("GET", "/api/triggers")
        items = data.get("data", data)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def ingest_trigger_webhook(
        self, public_token: str, payload: JsonObject
    ) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/triggers/webhooks/{public_token}",
            payload,
        )

    def inject_subagent_message(
        self,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> JsonObject:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/subagents/{instance_id}/inject",
            {"content": content},
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> JsonObject:
        request_body = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            request_body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            url=f"{self._base_url}{path}",
            data=request_body,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return {}
                data = json.loads(body)
                if isinstance(data, dict):
                    return data
                return {"data": data}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Failed to connect to server: {exc}") from exc


# Backward-compatible alias.
AgentTeamsApp = AgentTeamsClient


def _expect_str(value: JsonValue | None, field_name: str) -> str:
    if isinstance(value, str):
        return value
    raise RuntimeError(f"Expected string field '{field_name}' in server response")
