# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType

import httpx
import pytest

from relay_teams.interfaces.sdk.client import AsyncAgentTeamsClient

pytestmark = pytest.mark.asyncio


class _FakeSdkStreamResponse:
    def __init__(
        self,
        lines: list[str],
        *,
        error_response: httpx.Response | None = None,
    ) -> None:
        self._lines = lines
        self._error_response = error_response

    async def __aenter__(self) -> _FakeSdkStreamResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    def raise_for_status(self) -> None:
        if self._error_response is not None:
            raise httpx.HTTPStatusError(
                "stream failed",
                request=self._error_response.request,
                response=self._error_response,
            )
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeSdkHttpClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        stream_lines: list[str] | None = None,
        stream_error_response: httpx.Response | None = None,
    ) -> None:
        self._response = response or httpx.Response(
            200,
            json={"status": "ok"},
            request=httpx.Request("GET", "http://server.test/"),
        )
        self._stream_lines = [] if stream_lines is None else stream_lines
        self._stream_error_response = stream_error_response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.streams: list[tuple[str, str, dict[str, str]]] = []

    async def __aenter__(self) -> _FakeSdkHttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    async def request(
        self,
        method: str,
        url: str,
        *,
        content: bytes | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        self.requests.append((method, url, content, headers))
        return self._response

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> _FakeSdkStreamResponse:
        self.streams.append((method, url, headers))
        return _FakeSdkStreamResponse(
            self._stream_lines,
            error_response=self._stream_error_response,
        )


class _FakeAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body

    async def aclose(self) -> None:
        return None


async def test_reload_proxy_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.reload_proxy_config()

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/proxy:reload",
        "payload": None,
    }


async def test_probe_web_connectivity_passes_timeout_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.probe_web_connectivity(
        url="https://example.com",
        timeout_ms=2500,
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/web:probe",
        "payload": {"url": "https://example.com", "timeout_ms": 2500},
    }


async def test_get_proxy_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"http_proxy": "http://proxy.example:8080"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.get_proxy_config()

    assert response == {"http_proxy": "http://proxy.example:8080"}
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/proxy",
        "payload": None,
    }


async def test_delete_workspace_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.delete_workspace("project-alpha")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/workspaces/project-alpha",
        "payload": None,
    }


async def test_open_workspace_root_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.open_workspace_root("project-alpha")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/workspaces/project-alpha:open-root",
        "payload": None,
    }


async def test_open_workspace_root_supports_mount_query_parameter(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.open_workspace_root("project-alpha", mount="ops")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/workspaces/project-alpha:open-root?mount=ops",
        "payload": None,
    }


async def test_create_workspace_supports_mount_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"workspace_id": "project-alpha"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.create_workspace(
        workspace_id="project-alpha",
        default_mount_name="app",
        mounts=[
            {
                "mount_name": "app",
                "provider": "local",
                "provider_config": {"root_path": "/work/app"},
            }
        ],
    )

    assert response == {"workspace_id": "project-alpha"}
    assert captured == {
        "method": "POST",
        "path": "/api/workspaces",
        "payload": {
            "workspace_id": "project-alpha",
            "default_mount_name": "app",
            "mounts": [
                {
                    "mount_name": "app",
                    "provider": "local",
                    "provider_config": {"root_path": "/work/app"},
                }
            ],
        },
    }


async def test_update_workspace_supports_mount_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"workspace_id": "project-alpha"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.update_workspace(
        "project-alpha",
        default_mount_name="ops",
        mounts=[
            {
                "mount_name": "ops",
                "provider": "local",
                "provider_config": {"root_path": "/work/ops"},
            }
        ],
    )

    assert response == {"workspace_id": "project-alpha"}
    assert captured == {
        "method": "PUT",
        "path": "/api/workspaces/project-alpha",
        "payload": {
            "default_mount_name": "ops",
            "mounts": [
                {
                    "mount_name": "ops",
                    "provider": "local",
                    "provider_config": {"root_path": "/work/ops"},
                }
            ],
        },
    }


async def test_workspace_sdk_supports_mount_query_parameters(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        calls.append((method, path, payload))
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    _ = await client.get_workspace_tree("project-alpha", path=".", mount="ops")
    _ = await client.get_workspace_diffs("project-alpha", mount="ops")
    _ = await client.get_workspace_diff_file(
        "project-alpha",
        path="deploy.sh",
        mount="ops",
    )

    assert calls == [
        ("GET", "/api/workspaces/project-alpha/tree?path=.&mount=ops", None),
        ("GET", "/api/workspaces/project-alpha/diffs?mount=ops", None),
        (
            "GET",
            "/api/workspaces/project-alpha/diff?path=deploy.sh&mount=ops",
            None,
        ),
    ]


async def test_ssh_profile_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/workspace/ssh-profiles":
            return [{"ssh_profile_id": "prod"}]
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert await client.list_ssh_profiles() == [{"ssh_profile_id": "prod"}]
    assert await client.get_ssh_profile("prod") == {"status": "ok"}
    assert await client.save_ssh_profile("prod", {"host": "prod-alias"}) == {
        "status": "ok"
    }
    assert await client.reveal_ssh_profile_password("prod") == {"status": "ok"}
    assert await client.probe_ssh_profile({"ssh_profile_id": "prod"}) == {
        "status": "ok"
    }
    assert await client.delete_ssh_profile("prod") == {"status": "ok"}
    assert calls == [
        ("GET", "/api/system/configs/workspace/ssh-profiles", None),
        ("GET", "/api/system/configs/workspace/ssh-profiles/prod", None),
        (
            "PUT",
            "/api/system/configs/workspace/ssh-profiles/prod",
            {"config": {"host": "prod-alias"}},
        ),
        (
            "POST",
            "/api/system/configs/workspace/ssh-profiles/prod:reveal-password",
            None,
        ),
        (
            "POST",
            "/api/system/configs/workspace/ssh-profiles:probe",
            {"ssh_profile_id": "prod"},
        ),
        ("DELETE", "/api/system/configs/workspace/ssh-profiles/prod", None),
    ]


async def test_delete_workspace_supports_remove_directory(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.delete_workspace("project-alpha", remove_directory=True)

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/workspaces/project-alpha?remove_directory=true",
        "payload": {"force": True},
    }


async def test_get_web_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {
            "provider": "exa",
            "exa_api_key": None,
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.mdosch.de/",
            "searxng_instance_seeds": [
                "https://search.mdosch.de/",
                "https://search.seddens.net/",
                "https://search.wdpserver.com/",
            ],
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.get_web_config()

    assert response == {
        "provider": "exa",
        "exa_api_key": None,
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.mdosch.de/",
        "searxng_instance_seeds": [
            "https://search.mdosch.de/",
            "https://search.seddens.net/",
            "https://search.wdpserver.com/",
        ],
    }
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/web",
        "payload": None,
    }


async def test_get_github_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"token": None}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.get_github_config()

    assert response == {"token": None}
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/github",
        "payload": None,
    }


async def test_get_run_todo_calls_expected_endpoint(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"todo": {"run_id": "run-1", "items": []}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.get_run_todo("run-1")

    assert response == {"todo": {"run_id": "run-1", "items": []}}
    assert captured == {
        "method": "GET",
        "path": "/api/runs/run-1/todo",
        "payload": None,
    }


async def test_clawhub_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/clawhub/skills":
            return {"data": [{"skill_id": "skill-creator-2"}]}
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert await client.get_clawhub_config() == {"status": "ok"}
    assert await client.save_clawhub_config(token="ch_secret") == {"status": "ok"}
    assert await client.probe_clawhub_connectivity(
        token="ch_secret", timeout_ms=2500
    ) == {"status": "ok"}
    assert await client.list_clawhub_skills() == [{"skill_id": "skill-creator-2"}]
    assert await client.get_clawhub_skill("skill-creator-2") == {"status": "ok"}
    assert await client.save_clawhub_skill(
        "skill-creator-2",
        {"runtime_name": "skill-creator"},
    ) == {"status": "ok"}
    assert await client.delete_clawhub_skill("skill-creator-2") == {"status": "ok"}
    assert calls == [
        ("GET", "/api/system/configs/clawhub", None),
        ("PUT", "/api/system/configs/clawhub", {"token": "ch_secret"}),
        (
            "POST",
            "/api/system/configs/clawhub:probe",
            {"token": "ch_secret", "timeout_ms": 2500},
        ),
        ("GET", "/api/system/configs/clawhub/skills", None),
        ("GET", "/api/system/configs/clawhub/skills/skill-creator-2", None),
        (
            "PUT",
            "/api/system/configs/clawhub/skills/skill-creator-2",
            {"runtime_name": "skill-creator"},
        ),
        ("DELETE", "/api/system/configs/clawhub/skills/skill-creator-2", None),
    ]


async def test_clawhub_sdk_returns_empty_list_for_non_list_skill_payload(
    monkeypatch,
) -> None:
    client = AsyncAgentTeamsClient()

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        _ = (method, path, payload)
        return {"data": {"skill_id": "skill-creator-2"}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert await client.list_clawhub_skills() == []


async def test_save_proxy_config_passes_proxy_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.save_proxy_config(
        http_proxy="http://proxy.example:8080",
        https_proxy="http://proxy.example:8443",
        no_proxy="localhost,127.0.0.1",
        proxy_username="alice",
        proxy_password="secret",
    )

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/proxy",
        "payload": {
            "http_proxy": "http://proxy.example:8080",
            "https_proxy": "http://proxy.example:8443",
            "all_proxy": None,
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "ssl_verify": None,
        },
    }


async def test_save_web_config_passes_web_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.save_web_config(
        provider="exa",
        exa_api_key="secret",
        fallback_provider="searxng",
        searxng_instance_url="https://search.example.test/",
    )

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/web",
        "payload": {
            "provider": "exa",
            "exa_api_key": "secret",
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    }


async def test_save_web_config_defaults_to_searxng_fallback_provider(
    monkeypatch,
) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.save_web_config(
        provider="exa",
        exa_api_key="secret",
        searxng_instance_url="https://search.example.test/",
    )

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/web",
        "payload": {
            "provider": "exa",
            "exa_api_key": "secret",
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    }


async def test_save_github_config_passes_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.save_github_config(token="ghp_secret")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/github",
        "payload": {
            "token": "ghp_secret",
        },
    }


async def test_probe_web_connectivity_includes_proxy_override(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.probe_web_connectivity(
        url="https://example.com",
        timeout_ms=2500,
        https_proxy="http://proxy.example:8443",
        no_proxy="localhost,127.0.0.1",
        proxy_username="alice",
        proxy_password="secret",
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/web:probe",
        "payload": {
            "url": "https://example.com",
            "timeout_ms": 2500,
            "proxy_override": {
                "http_proxy": None,
                "https_proxy": "http://proxy.example:8443",
                "all_proxy": None,
                "no_proxy": "localhost,127.0.0.1",
                "proxy_username": "alice",
                "proxy_password": "secret",
                "ssl_verify": None,
            },
        },
    }


async def test_probe_github_connectivity_passes_payload(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.probe_github_connectivity(
        token="ghp_secret",
        timeout_ms=2500,
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/github:probe",
        "payload": {
            "token": "ghp_secret",
            "timeout_ms": 2500,
        },
    }


async def test_create_session_preserves_legacy_flat_metadata_payload(
    monkeypatch,
) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"session_id": "session-1", "workspace_id": "default"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.create_session(
        workspace_id="default",
        session_id="session-1",
        metadata={"project": "demo"},
    )

    assert response == {"session_id": "session-1", "workspace_id": "default"}
    assert captured == {
        "method": "POST",
        "path": "/api/sessions",
        "payload": {
            "session_id": "session-1",
            "workspace_id": "default",
            "metadata": {"project": "demo"},
        },
    }


async def test_delete_feishu_gateway_account_forces_delete_by_default(
    monkeypatch,
) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.delete_feishu_gateway_account("fsg_main")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/gateway/feishu/accounts/fsg_main",
        "payload": {"force": True},
    }


async def test_delete_wechat_gateway_account_forces_delete_by_default(
    monkeypatch,
) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = await client.delete_wechat_gateway_account("wx-account-1")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/gateway/wechat/accounts/wx-account-1",
        "payload": {"force": True},
    }


async def test_create_run_includes_target_role_id(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    captured: dict[str, object] = {}

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"run_id": "run-1", "session_id": "session-1"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    handle = await client.create_run(
        input="hello",
        session_id="session-1",
        target_role_id="writer",
    )

    assert handle.run_id == "run-1"
    assert handle.session_id == "session-1"
    assert captured == {
        "method": "POST",
        "path": "/api/runs",
        "payload": {
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
            "yolo": False,
            "target_role_id": "writer",
        },
    }


async def test_external_agent_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/agents":
            return [{"agent_id": "codex_local"}]
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert await client.list_external_agents() == [{"agent_id": "codex_local"}]
    assert await client.get_external_agent("codex_local") == {"status": "ok"}
    assert await client.save_external_agent(
        "codex_local", {"agent_id": "codex_local"}
    ) == {"status": "ok"}
    assert await client.test_external_agent("codex_local") == {"status": "ok"}
    assert await client.delete_external_agent("codex_local") == {"status": "ok"}
    assert calls == [
        ("GET", "/api/system/configs/agents", None),
        ("GET", "/api/system/configs/agents/codex_local", None),
        ("PUT", "/api/system/configs/agents/codex_local", {"agent_id": "codex_local"}),
        ("POST", "/api/system/configs/agents/codex_local:test", {}),
        ("DELETE", "/api/system/configs/agents/codex_local", None),
    ]


async def test_sdk_misc_endpoint_wrappers_cover_async_primitives(monkeypatch) -> None:
    client = AsyncAgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    async def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if path == "/api/runs/run-1/tool-approvals":
            return {"data": [{"approval_id": "approval-1"}, "ignored"]}
        if path == "/api/runs/run-1/questions":
            return {"data": [{"question_id": "question-1"}, "ignored"]}
        if method == "GET" and path == "/api/gateway/feishu/accounts":
            return {
                "data": [
                    {
                        "account_id": "feishu-1",
                        "name": "primary",
                        "display_name": "Primary",
                        "status": "enabled",
                        "source_config": {"kind": "feishu"},
                        "target_config": {"workspace_id": "default"},
                        "secret_config": {"app_secret": "configured"},
                        "secret_status": {"app_secret": True},
                    },
                    "ignored",
                ]
            }
        if method == "GET" and path == "/api/automation/projects":
            return {"data": [{"automation_project_id": "auto-1"}, "ignored"]}
        if path == "/api/automation/feishu-bindings":
            return {"data": [{"binding_id": "binding-1"}, "ignored"]}
        if path == "/api/gateway/wechat/accounts":
            return {"data": [{"account_id": "wechat-1"}, "ignored"]}
        if path == "/api/automation/projects/auto-1/sessions":
            return {"data": [{"session_id": "session-1"}, "ignored"]}
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert await client.health() == {"status": "ok"}
    assert await client.update_session_topology(
        "session-1",
        session_mode="orchestration",
        normal_root_role_id="coordinator",
        orchestration_preset_id="default",
    ) == {"status": "ok"}
    assert await client.list_tool_approvals("run-1") == [{"approval_id": "approval-1"}]
    assert await client.resolve_tool_approval(
        "run-1", "tool-call-1", "approve", "ok"
    ) == {"status": "ok"}
    assert await client.list_user_questions("run-1") == [{"question_id": "question-1"}]
    assert await client.answer_user_question(
        "run-1", "question-1", [{"choice": "yes"}]
    ) == {"status": "ok"}
    assert await client.create_tasks("run-1", [{"title": "Draft"}]) == {"status": "ok"}
    assert await client.list_delegated_tasks("run-1", include_root=True) == {
        "status": "ok"
    }
    assert await client.list_run_tasks("run-1") == {"status": "ok"}
    assert await client.update_task("task-1", objective="Ship it", title="Release") == {
        "status": "ok"
    }
    assert await client.inject_message("run-1", "continue") == {"status": "ok"}
    assert await client.stop_run("run-1") == {"status": "ok"}
    assert await client.resume_run("run-1") == {"status": "ok"}
    assert await client.stop_subagent("run-1", "agent-1") == {"status": "ok"}
    assert await client.create_feishu_gateway_account(
        name="primary",
        display_name="Primary",
        source_config={"kind": "feishu"},
        target_config={"workspace_id": "default"},
        secret_config={"app_secret": "secret"},
    ) == {"status": "ok"}
    assert await client.list_feishu_gateway_accounts() == [
        {
            "account_id": "feishu-1",
            "name": "primary",
            "display_name": "Primary",
            "status": "enabled",
            "source_config": {"kind": "feishu"},
            "target_config": {"workspace_id": "default"},
            "secret_config": {"app_secret": "configured"},
            "secret_status": {"app_secret": True},
        }
    ]
    assert await client.update_feishu_gateway_account(
        "feishu-1", {"enabled": True}
    ) == {"status": "ok"}
    assert await client.enable_feishu_gateway_account("feishu-1") == {"status": "ok"}
    assert await client.disable_feishu_gateway_account("feishu-1") == {"status": "ok"}
    assert await client.reload_feishu_gateway() == {"status": "ok"}
    assert await client.create_trigger(
        name="trigger-1",
        source_type="im",
        auth_policies=[{"kind": "token"}],
        public_token="public",
    ) == {"status": "ok"}
    assert await client.list_triggers() == [
        {
            "trigger_id": "feishu-1",
            "name": "primary",
            "display_name": "Primary",
            "source_type": "im",
            "status": "enabled",
            "source_config": {"kind": "feishu"},
            "target_config": {"workspace_id": "default"},
            "secret_config": {"app_secret": "configured"},
            "secret_status": {"app_secret": True},
        }
    ]
    with pytest.raises(RuntimeError, match="Trigger webhooks were removed"):
        await client.ingest_trigger_webhook("public", {"event": "ping"})
    assert await client.inject_subagent_message("run-1", "agent-1", "hello") == {
        "status": "ok"
    }
    assert await client.get_subagent_reflection("session-1", "agent-1") == {
        "status": "ok"
    }
    assert await client.refresh_subagent_reflection("session-1", "agent-1") == {
        "status": "ok"
    }
    assert await client.update_subagent_reflection(
        "session-1", "agent-1", "summary"
    ) == {"status": "ok"}
    assert await client.delete_subagent_reflection("session-1", "agent-1") == {
        "status": "ok"
    }
    assert await client.get_workspace_snapshot("workspace-1") == {"status": "ok"}
    assert await client.list_automation_projects() == [
        {"automation_project_id": "auto-1"}
    ]
    assert await client.list_automation_feishu_bindings() == [
        {"binding_id": "binding-1"}
    ]
    assert await client.list_wechat_gateway_accounts() == [{"account_id": "wechat-1"}]
    assert await client.start_wechat_gateway_login() == {"status": "ok"}
    assert await client.wait_wechat_gateway_login({"login_id": "login-1"}) == {
        "status": "ok"
    }
    assert await client.update_wechat_gateway_account(
        "wechat-1", {"enabled": True}
    ) == {"status": "ok"}
    assert await client.enable_wechat_gateway_account("wechat-1") == {"status": "ok"}
    assert await client.disable_wechat_gateway_account("wechat-1") == {"status": "ok"}
    assert await client.delete_wechat_gateway_account("wechat-1", force=False) == {
        "status": "ok"
    }
    assert await client.reload_wechat_gateway() == {"status": "ok"}
    assert await client.get_automation_project("auto-1") == {"status": "ok"}
    assert await client.create_automation_project({"name": "Auto"}) == {"status": "ok"}
    assert await client.update_automation_project("auto-1", {"name": "Auto"}) == {
        "status": "ok"
    }
    assert await client.run_automation_project("auto-1") == {"status": "ok"}
    assert await client.list_automation_project_sessions("auto-1") == [
        {"session_id": "session-1"}
    ]

    assert (
        "PATCH",
        "/api/sessions/session-1/topology",
        {
            "session_mode": "orchestration",
            "normal_root_role_id": "coordinator",
            "orchestration_preset_id": "default",
        },
    ) in calls
    assert (
        "POST",
        "/api/gateway/wechat/login/start",
        {},
    ) in calls
    assert (
        "DELETE",
        "/api/gateway/wechat/accounts/wechat-1",
        {"force": False},
    ) in calls


async def test_request_json_uses_async_http_client_and_normalizes_lists(
    monkeypatch,
) -> None:
    response = httpx.Response(
        200,
        json=[{"item": "one"}],
        request=httpx.Request("POST", "http://server.test/api/items"),
    )
    fake_client = _FakeSdkHttpClient(response=response)
    captured_kwargs: dict[str, object] = {}

    def fake_create_async_http_client(**kwargs: object) -> _FakeSdkHttpClient:
        captured_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        "relay_teams.interfaces.sdk.client.create_async_http_client",
        fake_create_async_http_client,
    )

    client = AsyncAgentTeamsClient(base_url="http://server.test/", timeout_seconds=7.5)
    result = await client._request_json("POST", "/api/items", {"name": "one"})

    assert result == {"data": [{"item": "one"}]}
    assert fake_client.requests == [
        (
            "POST",
            "http://server.test/api/items",
            b'{"name": "one"}',
            {"Accept": "application/json", "Content-Type": "application/json"},
        )
    ]
    assert captured_kwargs["timeout_seconds"] == 7.5


async def test_request_json_returns_empty_dict_for_empty_response(monkeypatch) -> None:
    response = httpx.Response(
        204,
        content=b"",
        request=httpx.Request("DELETE", "http://server.test/api/items/1"),
    )
    fake_client = _FakeSdkHttpClient(response=response)

    def fake_create_async_http_client(**kwargs: object) -> _FakeSdkHttpClient:
        _ = kwargs
        return fake_client

    monkeypatch.setattr(
        "relay_teams.interfaces.sdk.client.create_async_http_client",
        fake_create_async_http_client,
    )

    client = AsyncAgentTeamsClient(base_url="http://server.test")
    assert await client._request_json("DELETE", "/api/items/1") == {}


async def test_stream_run_events_filters_sse_lines(monkeypatch) -> None:
    fake_client = _FakeSdkHttpClient(
        stream_lines=[
            "",
            "event: ping",
            "data:",
            "data: []",
            'data: {"event_type":"run_completed","run_id":"run-1"}',
        ]
    )
    captured_kwargs: dict[str, object] = {}

    def fake_create_async_http_client(**kwargs: object) -> _FakeSdkHttpClient:
        captured_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        "relay_teams.interfaces.sdk.client.create_async_http_client",
        fake_create_async_http_client,
    )

    client = AsyncAgentTeamsClient(
        base_url="http://server.test", stream_timeout_seconds=12.0
    )
    events = [event async for event in client.stream_run_events("run-1")]

    assert events == [{"event_type": "run_completed", "run_id": "run-1"}]
    assert fake_client.streams == [
        (
            "GET",
            "http://server.test/api/runs/run-1/events",
            {"Accept": "text/event-stream"},
        )
    ]
    assert captured_kwargs["timeout_seconds"] == 12.0


async def test_stream_run_events_reads_streamed_error_body(monkeypatch) -> None:
    error_response = httpx.Response(
        503,
        request=httpx.Request("GET", "http://server.test/api/runs/run-1/events"),
        stream=_FakeAsyncByteStream(b"bad gateway"),
    )
    fake_client = _FakeSdkHttpClient(stream_error_response=error_response)

    def fake_create_async_http_client(**kwargs: object) -> _FakeSdkHttpClient:
        _ = kwargs
        return fake_client

    monkeypatch.setattr(
        "relay_teams.interfaces.sdk.client.create_async_http_client",
        fake_create_async_http_client,
    )

    client = AsyncAgentTeamsClient(base_url="http://server.test")

    with pytest.raises(
        RuntimeError,
        match="HTTP 503 while streaming run events: bad gateway",
    ):
        _ = [event async for event in client.stream_run_events("run-1")]
