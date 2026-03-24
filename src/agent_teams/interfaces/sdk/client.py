# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Generator
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, JsonValue

from agent_teams.env import load_proxy_env_config, sync_proxy_env_to_process_env


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

    def health(self) -> dict[str, JsonValue]:
        return self._request_json("GET", "/api/system/health")

    def reload_proxy_config(self) -> dict[str, JsonValue]:
        return self._request_json("POST", "/api/system/configs/proxy:reload")

    def get_proxy_config(self) -> dict[str, JsonValue]:
        return self._request_json("GET", "/api/system/configs/proxy")

    def get_web_config(self) -> dict[str, JsonValue]:
        return self._request_json("GET", "/api/system/configs/web")

    def save_proxy_config(
        self,
        *,
        http_proxy: str | None = None,
        https_proxy: str | None = None,
        all_proxy: str | None = None,
        no_proxy: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
        ssl_verify: bool | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "http_proxy": http_proxy,
            "https_proxy": https_proxy,
            "all_proxy": all_proxy,
            "no_proxy": no_proxy,
            "proxy_username": proxy_username,
            "proxy_password": proxy_password,
            "ssl_verify": ssl_verify,
        }
        return self._request_json("PUT", "/api/system/configs/proxy", payload)

    def save_web_config(
        self,
        *,
        provider: str = "exa",
        api_key: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "provider": provider,
            "api_key": api_key,
        }
        return self._request_json("PUT", "/api/system/configs/web", payload)

    def probe_web_connectivity(
        self,
        *,
        url: str,
        timeout_ms: int | None = None,
        http_proxy: str | None = None,
        https_proxy: str | None = None,
        all_proxy: str | None = None,
        no_proxy: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
        ssl_verify: bool | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"url": url}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        if any(
            value is not None
            for value in (
                http_proxy,
                https_proxy,
                all_proxy,
                no_proxy,
                proxy_username,
                proxy_password,
                ssl_verify,
            )
        ):
            payload["proxy_override"] = {
                "http_proxy": http_proxy,
                "https_proxy": https_proxy,
                "all_proxy": all_proxy,
                "no_proxy": no_proxy,
                "proxy_username": proxy_username,
                "proxy_password": proxy_password,
                "ssl_verify": ssl_verify,
            }
        return self._request_json("POST", "/api/system/configs/web:probe", payload)

    def create_session(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, JsonValue]:
        metadata_payload: dict[str, JsonValue] | None = None
        if metadata is not None:
            metadata_payload = {key: value for key, value in metadata.items()}
        payload: dict[str, JsonValue] = {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "metadata": metadata_payload,
        }
        return self._request_json(
            "POST",
            "/api/sessions",
            payload,
        )

    def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: str,
        orchestration_preset_id: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "session_mode": session_mode,
            "orchestration_preset_id": orchestration_preset_id,
        }
        return self._request_json(
            "PATCH",
            f"/api/sessions/{session_id}/topology",
            payload,
        )

    def create_run(
        self,
        intent: str,
        session_id: str,
        execution_mode: str = "ai",
        yolo: bool = False,
    ) -> RunHandle:
        payload: dict[str, JsonValue] = {
            "session_id": session_id,
            "intent": intent,
            "execution_mode": execution_mode,
            "yolo": yolo,
        }
        data = self._request_json("POST", "/api/runs", payload)
        return RunHandle(
            run_id=_expect_str(data.get("run_id"), "run_id"),
            session_id=_expect_str(data.get("session_id"), "session_id"),
        )

    def stream_run_events(
        self, run_id: str
    ) -> Generator[dict[str, JsonValue], None, None]:
        sync_proxy_env_to_process_env(load_proxy_env_config())
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
        except TimeoutError as exc:
            raise RuntimeError(
                f"Stream timed out after {self._stream_timeout_seconds}s"
            ) from exc

    def list_tool_approvals(self, run_id: str) -> list[dict[str, JsonValue]]:
        data = self._request_json("GET", f"/api/runs/{run_id}/tool-approvals")
        items = data.get("data", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def resolve_tool_approval(
        self, run_id: str, tool_call_id: str, action: str, feedback: str = ""
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve",
            {"action": action, "feedback": feedback},
        )

    def create_tasks(
        self,
        run_id: str,
        tasks: list[dict[str, JsonValue]] | None = None,
    ) -> dict[str, JsonValue]:
        tasks_payload: list[JsonValue] | None = None
        if tasks is not None:
            tasks_payload = [task for task in tasks]
        payload: dict[str, JsonValue] = {
            "tasks": tasks_payload,
        }
        return self._request_json(
            "POST",
            f"/api/tasks/runs/{run_id}",
            payload,
        )

    def list_delegated_tasks(
        self, run_id: str, include_root: bool = False
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "GET",
            f"/api/tasks/runs/{run_id}?include_root={'true' if include_root else 'false'}",
        )

    def list_run_tasks(
        self, run_id: str, include_root: bool = False
    ) -> dict[str, JsonValue]:
        return self.list_delegated_tasks(run_id, include_root=include_root)

    def update_task(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        title: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "objective": objective,
            "title": title,
        }
        return self._request_json(
            "PATCH",
            f"/api/tasks/{task_id}",
            payload,
        )

    def dispatch_task(
        self,
        task_id: str,
        *,
        role_id: str,
        prompt: str = "",
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/tasks/{task_id}/dispatch",
            {"role_id": role_id, "prompt": prompt},
        )

    def inject_message(self, run_id: str, content: str) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/inject",
            {"content": content},
        )

    def stop_run(self, run_id: str) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/stop",
            {"scope": "main"},
        )

    def resume_run(self, run_id: str) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}:resume",
            {},
        )

    def stop_subagent(self, run_id: str, instance_id: str) -> dict[str, JsonValue]:
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
        source_config: dict[str, JsonValue] | None = None,
        auth_policies: list[dict[str, JsonValue]] | None = None,
        target_config: dict[str, JsonValue] | None = None,
        public_token: str | None = None,
        enabled: bool = True,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "name": name,
            "source_type": source_type,
            "enabled": enabled,
        }
        if display_name is not None:
            payload["display_name"] = display_name
        if source_config is not None:
            payload["source_config"] = source_config
        if auth_policies is not None:
            policies_payload: list[JsonValue] = [policy for policy in auth_policies]
            payload["auth_policies"] = policies_payload
        if target_config is not None:
            payload["target_config"] = target_config
        if public_token is not None:
            payload["public_token"] = public_token
        return self._request_json("POST", "/api/triggers", payload)

    def list_triggers(self) -> list[dict[str, JsonValue]]:
        data = self._request_json("GET", "/api/triggers")
        items = data.get("data", data)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def ingest_trigger_webhook(
        self, public_token: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
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
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/runs/{run_id}/subagents/{instance_id}/inject",
            {"content": content},
        )

    def get_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "GET",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection",
        )

    def refresh_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection:refresh",
            {},
        )

    def update_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
        summary: str,
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "PATCH",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection",
            {"summary": summary},
        )

    def create_workspace(
        self,
        *,
        workspace_id: str,
        root_path: str,
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "POST",
            "/api/workspaces",
            {"workspace_id": workspace_id, "root_path": root_path},
        )

    def get_workspace_snapshot(self, workspace_id: str) -> dict[str, JsonValue]:
        return self._request_json("GET", f"/api/workspaces/{workspace_id}/snapshot")

    def get_workspace_tree(
        self,
        workspace_id: str,
        *,
        path: str = ".",
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "GET",
            f"/api/workspaces/{workspace_id}/tree?path={quote(path, safe='')}",
        )

    def get_workspace_diffs(self, workspace_id: str) -> dict[str, JsonValue]:
        return self._request_json("GET", f"/api/workspaces/{workspace_id}/diffs")

    def get_workspace_diff_file(
        self,
        workspace_id: str,
        *,
        path: str,
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "GET",
            f"/api/workspaces/{workspace_id}/diff?path={quote(path, safe='')}",
        )

    def delete_workspace(self, workspace_id: str) -> dict[str, JsonValue]:
        return self._request_json("DELETE", f"/api/workspaces/{workspace_id}")

    def delete_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, JsonValue]:
        return self._request_json(
            "DELETE",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection",
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, JsonValue]:
        sync_proxy_env_to_process_env(load_proxy_env_config())
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
        except TimeoutError as exc:
            raise RuntimeError(f"Request timed out: {method} {path}") from exc


# Backward-compatible alias.
AgentTeamsApp = AgentTeamsClient


def _expect_str(value: JsonValue | None, field_name: str) -> str:
    if isinstance(value, str):
        return value
    raise RuntimeError(f"Expected string field '{field_name}' in server response")
