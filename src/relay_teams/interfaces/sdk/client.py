# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.media import content_parts_from_text
from relay_teams.env import load_proxy_env_config
from relay_teams.net import create_async_http_client


class RunHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    session_id: str


class AsyncAgentTeamsClient:
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

    async def health(self) -> dict[str, JsonValue]:
        return await self._request_json("GET", "/api/system/health")

    async def reload_proxy_config(self) -> dict[str, JsonValue]:
        return await self._request_json("POST", "/api/system/configs/proxy:reload")

    async def get_proxy_config(self) -> dict[str, JsonValue]:
        return await self._request_json("GET", "/api/system/configs/proxy")

    async def list_external_agents(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", "/api/system/configs/agents")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def get_external_agent(self, agent_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET",
            f"/api/system/configs/agents/{quote(agent_id, safe='')}",
        )

    async def save_external_agent(
        self,
        agent_id: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PUT",
            f"/api/system/configs/agents/{quote(agent_id, safe='')}",
            payload,
        )

    async def delete_external_agent(self, agent_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "DELETE",
            f"/api/system/configs/agents/{quote(agent_id, safe='')}",
        )

    async def test_external_agent(self, agent_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/system/configs/agents/{quote(agent_id, safe='')}:test",
            {},
        )

    async def get_web_config(self) -> dict[str, JsonValue]:
        return await self._request_json("GET", "/api/system/configs/web")

    async def get_github_config(self) -> dict[str, JsonValue]:
        return await self._request_json("GET", "/api/system/configs/github")

    async def get_clawhub_config(self) -> dict[str, JsonValue]:
        return await self._request_json("GET", "/api/system/configs/clawhub")

    async def list_clawhub_skills(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", "/api/system/configs/clawhub/skills")
        raw = data.get("data")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    async def get_clawhub_skill(self, skill_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET",
            f"/api/system/configs/clawhub/skills/{quote(skill_id, safe='')}",
        )

    async def save_proxy_config(
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
        return await self._request_json("PUT", "/api/system/configs/proxy", payload)

    async def save_web_config(
        self,
        *,
        provider: str = "exa",
        exa_api_key: str | None = None,
        fallback_provider: str | None = "searxng",
        searxng_instance_url: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "provider": provider,
            "exa_api_key": exa_api_key,
            "fallback_provider": fallback_provider,
            "searxng_instance_url": searxng_instance_url,
        }
        return await self._request_json("PUT", "/api/system/configs/web", payload)

    async def save_github_config(
        self,
        *,
        token: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"token": token}
        return await self._request_json("PUT", "/api/system/configs/github", payload)

    async def save_clawhub_config(
        self,
        *,
        token: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"token": token}
        return await self._request_json("PUT", "/api/system/configs/clawhub", payload)

    async def save_clawhub_skill(
        self,
        skill_id: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PUT",
            f"/api/system/configs/clawhub/skills/{quote(skill_id, safe='')}",
            payload,
        )

    async def delete_clawhub_skill(self, skill_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "DELETE",
            f"/api/system/configs/clawhub/skills/{quote(skill_id, safe='')}",
        )

    async def probe_web_connectivity(
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
        return await self._request_json(
            "POST", "/api/system/configs/web:probe", payload
        )

    async def probe_github_connectivity(
        self,
        *,
        token: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {}
        if token is not None:
            payload["token"] = token
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await self._request_json(
            "POST", "/api/system/configs/github:probe", payload
        )

    async def probe_clawhub_connectivity(
        self,
        *,
        token: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {}
        if token is not None:
            payload["token"] = token
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await self._request_json(
            "POST", "/api/system/configs/clawhub:probe", payload
        )

    async def create_session(
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
        return await self._request_json(
            "POST",
            "/api/sessions",
            payload,
        )

    async def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: str,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "session_mode": session_mode,
            "normal_root_role_id": normal_root_role_id,
            "orchestration_preset_id": orchestration_preset_id,
        }
        return await self._request_json(
            "PATCH",
            f"/api/sessions/{session_id}/topology",
            payload,
        )

    async def create_run(
        self,
        input: str | list[JsonValue],
        session_id: str,
        execution_mode: str = "ai",
        yolo: bool = False,
        target_role_id: str | None = None,
    ) -> RunHandle:
        normalized_input: JsonValue = (
            [part.model_dump(mode="json") for part in content_parts_from_text(input)]
            if isinstance(input, str)
            else input
        )
        payload: dict[str, JsonValue] = {
            "session_id": session_id,
            "input": normalized_input,
            "execution_mode": execution_mode,
            "yolo": yolo,
            "target_role_id": target_role_id,
        }
        data = await self._request_json("POST", "/api/runs", payload)
        return RunHandle(
            run_id=_expect_str(data.get("run_id"), "run_id"),
            session_id=_expect_str(data.get("session_id"), "session_id"),
        )

    async def stream_run_events(
        self, run_id: str
    ) -> AsyncIterator[dict[str, JsonValue]]:
        url = f"{self._base_url}/api/runs/{run_id}/events"
        async with create_async_http_client(
            proxy_config=load_proxy_env_config(),
            timeout_seconds=self._stream_timeout_seconds,
            connect_timeout_seconds=self._stream_timeout_seconds,
        ) as client:
            try:
                async with client.stream(
                    "GET",
                    url,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        parsed = json.loads(payload)
                        if isinstance(parsed, dict):
                            yield parsed
            except httpx.HTTPStatusError as exc:
                body = (await exc.response.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"HTTP {exc.response.status_code} while streaming run events: {body}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    f"Stream timed out after {self._stream_timeout_seconds}s"
                ) from exc
            except httpx.RequestError as exc:
                raise RuntimeError(f"Failed to connect to server: {exc}") from exc

    async def list_tool_approvals(self, run_id: str) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", f"/api/runs/{run_id}/tool-approvals")
        items = data.get("data", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    async def get_run_todo(self, run_id: str) -> dict[str, JsonValue]:
        return await self._request_json("GET", f"/api/runs/{run_id}/todo")

    async def resolve_tool_approval(
        self, run_id: str, tool_call_id: str, action: str, feedback: str = ""
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve",
            {"action": action, "feedback": feedback},
        )

    async def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", f"/api/runs/{run_id}/questions")
        items = data.get("data", data)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    async def answer_user_question(
        self,
        run_id: str,
        question_id: str,
        answers: list[dict[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}/questions/{question_id}:answer",
            {"answers": answers},
        )

    async def create_tasks(
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
        return await self._request_json(
            "POST",
            f"/api/tasks/runs/{run_id}",
            payload,
        )

    async def list_delegated_tasks(
        self, run_id: str, include_root: bool = False
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET",
            f"/api/tasks/runs/{run_id}?include_root={'true' if include_root else 'false'}",
        )

    async def list_run_tasks(
        self, run_id: str, include_root: bool = False
    ) -> dict[str, JsonValue]:
        return await self.list_delegated_tasks(run_id, include_root=include_root)

    async def update_task(
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
        return await self._request_json(
            "PATCH",
            f"/api/tasks/{task_id}",
            payload,
        )

    async def inject_message(self, run_id: str, content: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}/inject",
            {"content": content},
        )

    async def stop_run(self, run_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}/stop",
            {"scope": "main"},
        )

    async def resume_run(self, run_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}:resume",
            {},
        )

    async def stop_subagent(
        self, run_id: str, instance_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}/stop",
            {"scope": "subagent", "instance_id": instance_id},
        )

    async def create_feishu_gateway_account(
        self,
        *,
        name: str,
        display_name: str | None = None,
        source_config: dict[str, JsonValue] | None = None,
        target_config: dict[str, JsonValue] | None = None,
        secret_config: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "name": name,
            "enabled": enabled,
        }
        if display_name is not None:
            payload["display_name"] = display_name
        if source_config is not None:
            payload["source_config"] = source_config
        if target_config is not None:
            payload["target_config"] = target_config
        if secret_config is not None:
            payload["secret_config"] = {
                key: value for key, value in secret_config.items()
            }
        return await self._request_json("POST", "/api/gateway/feishu/accounts", payload)

    async def list_feishu_gateway_accounts(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", "/api/gateway/feishu/accounts")
        items = data.get("data", data)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    async def update_feishu_gateway_account(
        self,
        account_id: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PATCH",
            f"/api/gateway/feishu/accounts/{quote(account_id, safe='')}",
            payload,
        )

    async def enable_feishu_gateway_account(
        self, account_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/gateway/feishu/accounts/{quote(account_id, safe='')}:enable",
            {},
        )

    async def disable_feishu_gateway_account(
        self, account_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/gateway/feishu/accounts/{quote(account_id, safe='')}:disable",
            {},
        )

    async def delete_feishu_gateway_account(
        self,
        account_id: str,
        *,
        force: bool = True,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "DELETE",
            f"/api/gateway/feishu/accounts/{quote(account_id, safe='')}",
            {"force": force},
        )

    async def reload_feishu_gateway(self) -> dict[str, JsonValue]:
        return await self._request_json("POST", "/api/gateway/feishu/reload", {})

    async def create_trigger(
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
        _ = (source_type, auth_policies, public_token)
        return await self.create_feishu_gateway_account(
            name=name,
            display_name=display_name,
            source_config=source_config,
            target_config=target_config,
            enabled=enabled,
        )

    async def list_triggers(self) -> list[dict[str, JsonValue]]:
        accounts = await self.list_feishu_gateway_accounts()
        normalized: list[dict[str, JsonValue]] = []
        for account in accounts:
            normalized.append(
                {
                    "trigger_id": str(account.get("account_id") or ""),
                    "name": str(account.get("name") or ""),
                    "display_name": str(
                        account.get("display_name") or account.get("name") or ""
                    ),
                    "source_type": "im",
                    "status": str(account.get("status") or "disabled"),
                    "source_config": account.get("source_config") or {},
                    "target_config": account.get("target_config") or {},
                    "secret_config": account.get("secret_config") or None,
                    "secret_status": account.get("secret_status") or None,
                }
            )
        return normalized

    async def ingest_trigger_webhook(
        self, public_token: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        _ = (public_token, payload)
        raise RuntimeError(
            "Trigger webhooks were removed. Use the gateway-specific IM integrations instead."
        )

    async def inject_subagent_message(
        self,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/runs/{run_id}/subagents/{instance_id}/inject",
            {"content": content},
        )

    async def get_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection",
        )

    async def refresh_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection:refresh",
            {},
        )

    async def update_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
        summary: str,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PATCH",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection",
            {"summary": summary},
        )

    async def create_workspace(
        self,
        *,
        workspace_id: str,
        root_path: str | None = None,
        default_mount_name: str | None = None,
        mounts: list[dict[str, JsonValue]] | None = None,
    ) -> dict[str, JsonValue]:
        payload: dict[str, object] = {"workspace_id": workspace_id}
        if root_path is not None:
            payload["root_path"] = root_path
        if default_mount_name is not None:
            payload["default_mount_name"] = default_mount_name
        if mounts is not None:
            payload["mounts"] = mounts
        return await self._request_json(
            "POST",
            "/api/workspaces",
            payload,
        )

    async def update_workspace(
        self,
        workspace_id: str,
        *,
        default_mount_name: str,
        mounts: list[dict[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PUT",
            f"/api/workspaces/{quote(workspace_id, safe='')}",
            {
                "default_mount_name": default_mount_name,
                "mounts": mounts,
            },
        )

    async def get_workspace_snapshot(self, workspace_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET", f"/api/workspaces/{workspace_id}/snapshot"
        )

    async def open_workspace_root(
        self,
        workspace_id: str,
        *,
        mount: str | None = None,
    ) -> dict[str, JsonValue]:
        path = f"/api/workspaces/{workspace_id}:open-root"
        if mount is not None:
            path += f"?mount={quote(mount, safe='')}"
        return await self._request_json(
            "POST",
            path,
        )

    async def get_workspace_tree(
        self,
        workspace_id: str,
        *,
        path: str = ".",
        mount: str | None = None,
    ) -> dict[str, JsonValue]:
        query = f"path={quote(path, safe='')}"
        if mount is not None:
            query += f"&mount={quote(mount, safe='')}"
        return await self._request_json(
            "GET",
            f"/api/workspaces/{workspace_id}/tree?{query}",
        )

    async def get_workspace_diffs(
        self,
        workspace_id: str,
        *,
        mount: str | None = None,
    ) -> dict[str, JsonValue]:
        path = f"/api/workspaces/{workspace_id}/diffs"
        if mount is not None:
            path += f"?mount={quote(mount, safe='')}"
        return await self._request_json("GET", path)

    async def get_workspace_diff_file(
        self,
        workspace_id: str,
        *,
        path: str,
        mount: str | None = None,
    ) -> dict[str, JsonValue]:
        query = f"path={quote(path, safe='')}"
        if mount is not None:
            query += f"&mount={quote(mount, safe='')}"
        return await self._request_json(
            "GET",
            f"/api/workspaces/{workspace_id}/diff?{query}",
        )

    async def list_ssh_profiles(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json(
            "GET", "/api/system/configs/workspace/ssh-profiles"
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def get_ssh_profile(self, ssh_profile_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET",
            f"/api/system/configs/workspace/ssh-profiles/{quote(ssh_profile_id, safe='')}",
        )

    async def save_ssh_profile(
        self,
        ssh_profile_id: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PUT",
            f"/api/system/configs/workspace/ssh-profiles/{quote(ssh_profile_id, safe='')}",
            {"config": payload},
        )

    async def reveal_ssh_profile_password(
        self, ssh_profile_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/system/configs/workspace/ssh-profiles/{quote(ssh_profile_id, safe='')}:reveal-password",
        )

    async def probe_ssh_profile(
        self,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            "/api/system/configs/workspace/ssh-profiles:probe",
            payload,
        )

    async def delete_ssh_profile(self, ssh_profile_id: str) -> dict[str, JsonValue]:
        return await self._request_json(
            "DELETE",
            f"/api/system/configs/workspace/ssh-profiles/{quote(ssh_profile_id, safe='')}",
        )

    async def delete_workspace(
        self,
        workspace_id: str,
        *,
        remove_directory: bool = False,
    ) -> dict[str, JsonValue]:
        query = "?remove_directory=true" if remove_directory else ""
        payload: dict[str, JsonValue] | None = (
            {"force": True} if remove_directory else None
        )
        return await self._request_json(
            "DELETE",
            f"/api/workspaces/{workspace_id}{query}",
            payload,
        )

    async def list_automation_projects(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", "/api/automation/projects")
        raw = data.get("data")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    async def list_automation_feishu_bindings(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", "/api/automation/feishu-bindings")
        raw = data.get("data")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    async def list_wechat_gateway_accounts(self) -> list[dict[str, JsonValue]]:
        data = await self._request_json("GET", "/api/gateway/wechat/accounts")
        raw = data.get("data", data)
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    async def start_wechat_gateway_login(
        self,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        request_payload = {} if payload is None else payload
        return await self._request_json(
            "POST", "/api/gateway/wechat/login/start", request_payload
        )

    async def wait_wechat_gateway_login(
        self,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST", "/api/gateway/wechat/login/wait", payload
        )

    async def update_wechat_gateway_account(
        self,
        account_id: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PATCH",
            f"/api/gateway/wechat/accounts/{account_id}",
            payload,
        )

    async def enable_wechat_gateway_account(
        self, account_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/gateway/wechat/accounts/{account_id}:enable",
            {},
        )

    async def disable_wechat_gateway_account(
        self, account_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/gateway/wechat/accounts/{account_id}:disable",
            {},
        )

    async def delete_wechat_gateway_account(
        self,
        account_id: str,
        *,
        force: bool = True,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "DELETE",
            f"/api/gateway/wechat/accounts/{account_id}",
            {"force": force},
        )

    async def reload_wechat_gateway(self) -> dict[str, JsonValue]:
        return await self._request_json("POST", "/api/gateway/wechat/reload", {})

    async def get_automation_project(
        self, automation_project_id: str
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "GET",
            f"/api/automation/projects/{automation_project_id}",
        )

    async def create_automation_project(
        self,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json("POST", "/api/automation/projects", payload)

    async def update_automation_project(
        self,
        automation_project_id: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "PATCH",
            f"/api/automation/projects/{automation_project_id}",
            payload,
        )

    async def run_automation_project(
        self,
        automation_project_id: str,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "POST",
            f"/api/automation/projects/{automation_project_id}:run",
            {},
        )

    async def list_automation_project_sessions(
        self,
        automation_project_id: str,
    ) -> list[dict[str, JsonValue]]:
        data = await self._request_json(
            "GET",
            f"/api/automation/projects/{automation_project_id}/sessions",
        )
        raw = data.get("data")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    async def delete_subagent_reflection(
        self,
        session_id: str,
        instance_id: str,
    ) -> dict[str, JsonValue]:
        return await self._request_json(
            "DELETE",
            f"/api/sessions/{session_id}/agents/{instance_id}/reflection",
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, JsonValue]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"

        async with create_async_http_client(
            proxy_config=load_proxy_env_config(),
            timeout_seconds=self._timeout_seconds,
            connect_timeout_seconds=self._timeout_seconds,
        ) as client:
            try:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    content=json.dumps(payload).encode("utf-8")
                    if payload is not None
                    else None,
                    headers=headers,
                )
                response.raise_for_status()
                if not response.content:
                    return {}
                data = response.json()
                if isinstance(data, dict):
                    return data
                return {"data": data}
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"HTTP {exc.response.status_code} {method} {path}: {exc.response.text}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise RuntimeError(f"Request timed out: {method} {path}") from exc
            except httpx.RequestError as exc:
                raise RuntimeError(f"Failed to connect to server: {exc}") from exc
        raise RuntimeError(f"Unexpected response handling exit: {method} {path}")


def _expect_str(value: JsonValue | None, field_name: str) -> str:
    if isinstance(value, str):
        return value
    raise RuntimeError(f"Expected string field '{field_name}' in server response")
