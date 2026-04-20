# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.interfaces.sdk.client import AgentTeamsClient


def test_reload_proxy_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.reload_proxy_config()

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/proxy:reload",
        "payload": None,
    }


def test_probe_web_connectivity_passes_timeout_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.probe_web_connectivity(
        url="https://example.com",
        timeout_ms=2500,
    )

    assert response == {"ok": True}
    assert captured == {
        "method": "POST",
        "path": "/api/system/configs/web:probe",
        "payload": {"url": "https://example.com", "timeout_ms": 2500},
    }


def test_get_proxy_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"http_proxy": "http://proxy.example:8080"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.get_proxy_config()

    assert response == {"http_proxy": "http://proxy.example:8080"}
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/proxy",
        "payload": None,
    }


def test_delete_workspace_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.delete_workspace("project-alpha")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/workspaces/project-alpha",
        "payload": None,
    }


def test_open_workspace_root_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.open_workspace_root("project-alpha")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/workspaces/project-alpha:open-root",
        "payload": None,
    }


def test_open_workspace_root_supports_mount_query_parameter(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.open_workspace_root("project-alpha", mount="ops")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "POST",
        "path": "/api/workspaces/project-alpha:open-root?mount=ops",
        "payload": None,
    }


def test_create_workspace_supports_mount_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"workspace_id": "project-alpha"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.create_workspace(
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


def test_update_workspace_supports_mount_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"workspace_id": "project-alpha"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.update_workspace(
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


def test_workspace_sdk_supports_mount_query_parameters(monkeypatch) -> None:
    client = AgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        calls.append((method, path, payload))
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    _ = client.get_workspace_tree("project-alpha", path=".", mount="ops")
    _ = client.get_workspace_diffs("project-alpha", mount="ops")
    _ = client.get_workspace_diff_file(
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


def test_ssh_profile_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/workspace/ssh-profiles":
            return [{"ssh_profile_id": "prod"}]
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert client.list_ssh_profiles() == [{"ssh_profile_id": "prod"}]
    assert client.get_ssh_profile("prod") == {"status": "ok"}
    assert client.save_ssh_profile("prod", {"host": "prod-alias"}) == {"status": "ok"}
    assert client.delete_ssh_profile("prod") == {"status": "ok"}
    assert calls == [
        ("GET", "/api/system/configs/workspace/ssh-profiles", None),
        ("GET", "/api/system/configs/workspace/ssh-profiles/prod", None),
        (
            "PUT",
            "/api/system/configs/workspace/ssh-profiles/prod",
            {"config": {"host": "prod-alias"}},
        ),
        ("DELETE", "/api/system/configs/workspace/ssh-profiles/prod", None),
    ]


def test_delete_workspace_supports_remove_directory(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.delete_workspace("project-alpha", remove_directory=True)

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/workspaces/project-alpha?remove_directory=true",
        "payload": {"force": True},
    }


def test_get_web_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
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

    response = client.get_web_config()

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


def test_get_github_config_calls_expected_endpoint(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"token": None}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.get_github_config()

    assert response == {"token": None}
    assert captured == {
        "method": "GET",
        "path": "/api/system/configs/github",
        "payload": None,
    }


def test_clawhub_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/clawhub/skills":
            return {"data": [{"skill_id": "skill-creator-2"}]}
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert client.get_clawhub_config() == {"status": "ok"}
    assert client.save_clawhub_config(token="ch_secret") == {"status": "ok"}
    assert client.probe_clawhub_connectivity(token="ch_secret", timeout_ms=2500) == {
        "status": "ok"
    }
    assert client.list_clawhub_skills() == [{"skill_id": "skill-creator-2"}]
    assert client.get_clawhub_skill("skill-creator-2") == {"status": "ok"}
    assert client.save_clawhub_skill(
        "skill-creator-2",
        {"runtime_name": "skill-creator"},
    ) == {"status": "ok"}
    assert client.delete_clawhub_skill("skill-creator-2") == {"status": "ok"}
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


def test_clawhub_sdk_returns_empty_list_for_non_list_skill_payload(monkeypatch) -> None:
    client = AgentTeamsClient()

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        _ = (method, path, payload)
        return {"data": {"skill_id": "skill-creator-2"}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert client.list_clawhub_skills() == []


def test_save_proxy_config_passes_proxy_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_proxy_config(
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


def test_save_web_config_passes_web_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_web_config(
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


def test_save_web_config_defaults_to_searxng_fallback_provider(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_web_config(
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


def test_save_github_config_passes_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.save_github_config(token="ghp_secret")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "PUT",
        "path": "/api/system/configs/github",
        "payload": {
            "token": "ghp_secret",
        },
    }


def test_probe_web_connectivity_includes_proxy_override(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.probe_web_connectivity(
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


def test_probe_github_connectivity_passes_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.probe_github_connectivity(
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


def test_create_session_preserves_legacy_flat_metadata_payload(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"session_id": "session-1", "workspace_id": "default"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.create_session(
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


def test_delete_feishu_gateway_account_forces_delete_by_default(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.delete_feishu_gateway_account("fsg_main")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/gateway/feishu/accounts/fsg_main",
        "payload": {"force": True},
    }


def test_delete_wechat_gateway_account_forces_delete_by_default(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    response = client.delete_wechat_gateway_account("wx-account-1")

    assert response == {"status": "ok"}
    assert captured == {
        "method": "DELETE",
        "path": "/api/gateway/wechat/accounts/wx-account-1",
        "payload": {"force": True},
    }


def test_create_run_includes_target_role_id(monkeypatch) -> None:
    client = AgentTeamsClient()
    captured: dict[str, object] = {}

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"run_id": "run-1", "session_id": "session-1"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    handle = client.create_run(
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


def test_external_agent_sdk_calls_expected_endpoints(monkeypatch) -> None:
    client = AgentTeamsClient()
    calls: list[tuple[str, str, object | None]] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object] | list[object]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/system/configs/agents":
            return [{"agent_id": "codex_local"}]
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert client.list_external_agents() == [{"agent_id": "codex_local"}]
    assert client.get_external_agent("codex_local") == {"status": "ok"}
    assert client.save_external_agent("codex_local", {"agent_id": "codex_local"}) == {
        "status": "ok"
    }
    assert client.test_external_agent("codex_local") == {"status": "ok"}
    assert client.delete_external_agent("codex_local") == {"status": "ok"}
    assert calls == [
        ("GET", "/api/system/configs/agents", None),
        ("GET", "/api/system/configs/agents/codex_local", None),
        ("PUT", "/api/system/configs/agents/codex_local", {"agent_id": "codex_local"}),
        ("POST", "/api/system/configs/agents/codex_local:test", {}),
        ("DELETE", "/api/system/configs/agents/codex_local", None),
    ]
