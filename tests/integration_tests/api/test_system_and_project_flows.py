from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from relay_teams.env.web_config_models import DEFAULT_SEARXNG_INSTANCE_SEEDS

from integration_tests.support.api_helpers import stream_run_until_terminal


def test_system_config_roundtrips_and_prompt_preview(
    api_client: httpx.Client,
) -> None:
    notification_response = api_client.put(
        "/api/system/configs/notifications",
        json={
            "config": {
                "run_stopped": {
                    "enabled": True,
                    "channels": ["browser"],
                }
            }
        },
    )
    notification_response.raise_for_status()

    notifications_payload = api_client.get("/api/system/configs/notifications").json()
    assert notifications_payload["run_stopped"]["enabled"] is True
    assert notifications_payload["run_stopped"]["channels"] == ["browser"]

    exa_web_api_key = f"web-exa-{uuid4().hex[:8]}"
    web_response = api_client.put(
        "/api/system/configs/web",
        json={
            "provider": "exa",
            "exa_api_key": exa_web_api_key,
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    )
    web_response.raise_for_status()
    web_payload = api_client.get("/api/system/configs/web").json()
    assert web_payload == {
        "provider": "exa",
        "exa_api_key": exa_web_api_key,
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.example.test/",
        "searxng_instance_seeds": list(DEFAULT_SEARXNG_INSTANCE_SEEDS),
    }

    github_token = f"ghp_{uuid4().hex[:12]}"
    github_response = api_client.put(
        "/api/system/configs/github",
        json={"token": github_token},
    )
    github_response.raise_for_status()
    github_payload = api_client.get("/api/system/configs/github").json()
    assert github_payload == {"token": github_token}

    proxy_response = api_client.put(
        "/api/system/configs/proxy",
        json={
            "http_proxy": "http://127.0.0.1:7890",
            "https_proxy": "http://127.0.0.1:7890",
            "all_proxy": None,
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": None,
            "proxy_password": None,
            "ssl_verify": False,
        },
    )
    proxy_response.raise_for_status()
    proxy_payload = api_client.get("/api/system/configs/proxy").json()
    assert proxy_payload["http_proxy"] == "http://127.0.0.1:7890"
    assert proxy_payload["https_proxy"] == "http://127.0.0.1:7890"
    assert proxy_payload["no_proxy"] == "localhost,127.0.0.1"
    assert proxy_payload["ssl_verify"] is False

    preview_response = api_client.post(
        "/api/prompts:preview",
        json={
            "role_id": "MainAgent",
            "objective": "Summarize issue 158.",
            "shared_state": {"ticket": "158"},
        },
    )
    preview_response.raise_for_status()
    preview_payload = preview_response.json()
    assert preview_payload["role_id"] == "MainAgent"
    assert preview_payload["objective"] == "Summarize issue 158."
    assert isinstance(preview_payload["runtime_system_prompt"], str)
    assert preview_payload["runtime_system_prompt"]
    assert isinstance(preview_payload["provider_system_prompt"], str)
    assert preview_payload["provider_system_prompt"]
    assert isinstance(preview_payload["user_prompt"], str)


def test_external_agent_routes_support_stdio_probe(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    agent_id = f"probe_agent_{uuid4().hex[:8]}"
    probe_script_path = _write_acp_probe_script(tmp_path)

    save_response = api_client.put(
        f"/api/system/configs/agents/{agent_id}",
        json={
            "agent_id": agent_id,
            "name": "Probe Agent",
            "description": "Stdio probe agent for integration coverage.",
            "transport": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(probe_script_path)],
                "env": [],
            },
        },
    )
    save_response.raise_for_status()
    saved_payload = save_response.json()
    assert saved_payload["agent_id"] == agent_id
    assert saved_payload["transport"]["transport"] == "stdio"
    assert saved_payload["transport"]["command"] == sys.executable

    get_response = api_client.get(f"/api/system/configs/agents/{agent_id}")
    get_response.raise_for_status()
    get_payload = get_response.json()
    assert get_payload["agent_id"] == agent_id
    assert get_payload["name"] == "Probe Agent"
    assert get_payload["transport"]["args"] == [str(probe_script_path)]

    test_response = api_client.post(f"/api/system/configs/agents/{agent_id}:test")
    test_response.raise_for_status()
    test_payload = test_response.json()
    assert test_payload["ok"] is True
    assert test_payload["protocol_version"] == 1
    assert test_payload["agent_name"] == "Integration ACP Probe"
    assert test_payload["agent_version"] == "1.0.0"

    delete_response = api_client.delete(f"/api/system/configs/agents/{agent_id}")
    delete_response.raise_for_status()
    assert delete_response.json() == {"status": "ok"}

    missing_response = api_client.get(f"/api/system/configs/agents/{agent_id}")
    assert missing_response.status_code == 404


def test_workspace_automation_and_feishu_gateway_routes(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    workspace_id = f"ws-{uuid4().hex[:8]}"
    workspace_root = _create_git_workspace(tmp_path / workspace_id)

    create_workspace_response = api_client.post(
        "/api/workspaces",
        json={
            "workspace_id": workspace_id,
            "root_path": str(workspace_root),
        },
    )
    create_workspace_response.raise_for_status()

    snapshot_response = api_client.get(f"/api/workspaces/{workspace_id}/snapshot")
    snapshot_response.raise_for_status()
    snapshot_payload = snapshot_response.json()
    assert snapshot_payload["workspace_id"] == workspace_id
    tree_children = snapshot_payload["tree"]["children"]
    assert any(child["path"] == "README.md" for child in tree_children)
    assert any(child["path"] == "src" for child in tree_children)

    tree_response = api_client.get(
        f"/api/workspaces/{workspace_id}/tree", params={"path": "src"}
    )
    tree_response.raise_for_status()
    tree_payload = tree_response.json()
    assert tree_payload["workspace_id"] == workspace_id
    assert [child["path"] for child in tree_payload["children"]] == ["src/main.py"]

    diffs_response = api_client.get(f"/api/workspaces/{workspace_id}/diffs")
    diffs_response.raise_for_status()
    diffs_payload = diffs_response.json()
    assert diffs_payload["workspace_id"] == workspace_id
    assert diffs_payload["is_git_repository"] is True
    diff_paths = [file["path"] for file in diffs_payload["diff_files"]]
    assert "src/main.py" in diff_paths

    diff_file_response = api_client.get(
        f"/api/workspaces/{workspace_id}/diff",
        params={"path": "src/main.py"},
    )
    diff_file_response.raise_for_status()
    diff_file_payload = diff_file_response.json()
    assert diff_file_payload["path"] == "src/main.py"
    assert "-print('v1')" in diff_file_payload["diff"]
    assert "+print('v2')" in diff_file_payload["diff"]

    automation_name = f"automation-{uuid4().hex[:8]}"
    create_project_response = api_client.post(
        "/api/automation/projects",
        json={
            "name": automation_name,
            "display_name": "Integration Automation",
            "workspace_id": workspace_id,
            "prompt": "Summarize the integration workspace.",
            "schedule_mode": "cron",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
            "run_config": {
                "session_mode": "normal",
                "execution_mode": "ai",
                "yolo": True,
                "thinking": {"enabled": False, "effort": None},
            },
            "enabled": False,
        },
    )
    create_project_response.raise_for_status()
    project_payload = create_project_response.json()
    automation_project_id = str(project_payload["automation_project_id"])
    assert project_payload["workspace_id"] == workspace_id
    assert project_payload["status"] == "disabled"

    update_project_response = api_client.patch(
        f"/api/automation/projects/{automation_project_id}",
        json={
            "display_name": "Updated Integration Automation",
            "prompt": "Summarize the updated integration workspace.",
        },
    )
    update_project_response.raise_for_status()
    updated_project_payload = update_project_response.json()
    assert updated_project_payload["display_name"] == "Updated Integration Automation"
    assert (
        updated_project_payload["prompt"]
        == "Summarize the updated integration workspace."
    )

    enable_project_response = api_client.post(
        f"/api/automation/projects/{automation_project_id}:enable"
    )
    enable_project_response.raise_for_status()
    assert enable_project_response.json()["status"] == "enabled"

    run_project_response = api_client.post(
        f"/api/automation/projects/{automation_project_id}:run"
    )
    run_project_response.raise_for_status()
    run_project_payload = run_project_response.json()
    session_id = str(run_project_payload["session_id"])
    run_id = str(run_project_payload.get("run_id") or "")
    assert session_id
    if run_id:
        events = stream_run_until_terminal(api_client, run_id=run_id)
        assert events[-1]["event_type"] == "run_completed"

    project_sessions_response = api_client.get(
        f"/api/automation/projects/{automation_project_id}/sessions"
    )
    project_sessions_response.raise_for_status()
    project_sessions_payload = project_sessions_response.json()
    assert any(
        str(session.get("session_id") or "") == session_id
        for session in project_sessions_payload
    )

    disable_project_response = api_client.post(
        f"/api/automation/projects/{automation_project_id}:disable"
    )
    disable_project_response.raise_for_status()
    assert disable_project_response.json()["status"] == "disabled"

    delete_project_response = api_client.delete(
        f"/api/automation/projects/{automation_project_id}"
    )
    delete_project_response.raise_for_status()
    assert delete_project_response.json() == {"status": "ok"}

    feishu_account_name = f"feishu-{uuid4().hex[:8]}"
    create_feishu_response = api_client.post(
        "/api/gateway/feishu/accounts",
        json={
            "name": feishu_account_name,
            "display_name": "Integration Feishu",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_app",
                "app_name": "CLI App",
            },
            "target_config": {
                "workspace_id": workspace_id,
                "session_mode": "normal",
            },
            "secret_config": {
                "app_secret": "feishu-secret",
            },
            "enabled": False,
        },
    )
    create_feishu_response.raise_for_status()
    feishu_payload = create_feishu_response.json()
    account_id = str(feishu_payload["account_id"])
    assert feishu_payload["status"] == "disabled"
    assert feishu_payload["display_name"] == "Integration Feishu"

    update_feishu_response = api_client.patch(
        f"/api/gateway/feishu/accounts/{account_id}",
        json={
            "display_name": "Updated Integration Feishu",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_app",
                "app_name": "Updated CLI App",
            },
            "target_config": {
                "workspace_id": workspace_id,
                "session_mode": "normal",
            },
        },
    )
    update_feishu_response.raise_for_status()
    updated_feishu_payload = update_feishu_response.json()
    assert updated_feishu_payload["display_name"] == "Updated Integration Feishu"
    assert updated_feishu_payload["source_config"]["app_name"] == "Updated CLI App"

    enable_feishu_response = api_client.post(
        f"/api/gateway/feishu/accounts/{account_id}:enable"
    )
    enable_feishu_response.raise_for_status()
    assert enable_feishu_response.json()["status"] == "enabled"

    disable_feishu_response = api_client.post(
        f"/api/gateway/feishu/accounts/{account_id}:disable"
    )
    disable_feishu_response.raise_for_status()
    assert disable_feishu_response.json()["status"] == "disabled"

    reload_feishu_response = api_client.post("/api/gateway/feishu/reload")
    reload_feishu_response.raise_for_status()
    assert reload_feishu_response.json() == {"status": "ok"}

    delete_feishu_response = api_client.delete(
        f"/api/gateway/feishu/accounts/{account_id}"
    )
    delete_feishu_response.raise_for_status()
    assert delete_feishu_response.json() == {"status": "ok"}


def _write_acp_probe_script(tmp_path: Path) -> Path:
    script_path = tmp_path / "acp_probe.py"
    script_path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "",
                "import json",
                "import sys",
                "",
                "for raw_line in sys.stdin:",
                "    line = raw_line.strip()",
                "    if not line:",
                "        continue",
                "    message = json.loads(line)",
                "    if message.get('method') != 'initialize':",
                "        continue",
                "    response = {",
                "        'jsonrpc': '2.0',",
                "        'id': message.get('id'),",
                "        'result': {",
                "            'protocolVersion': 1,",
                "            'agentInfo': {",
                "                'name': 'Integration ACP Probe',",
                "                'version': '1.0.0',",
                "            },",
                "        },",
                "    }",
                "    sys.stdout.write(json.dumps(response) + '\\n')",
                "    sys.stdout.flush()",
            )
        ),
        encoding="utf-8",
    )
    return script_path


def _create_git_workspace(workspace_root: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for workspace diff integration coverage")

    (workspace_root / "src").mkdir(parents=True)
    (workspace_root / "README.md").write_text(
        "# Integration Workspace\n",
        encoding="utf-8",
    )
    (workspace_root / "src" / "main.py").write_text(
        "print('v1')\n",
        encoding="utf-8",
    )
    _run_git_command(workspace_root, "init")
    _run_git_command(workspace_root, "config", "user.name", "Integration Test")
    _run_git_command(
        workspace_root,
        "config",
        "user.email",
        "integration@example.com",
    )
    _run_git_command(workspace_root, "add", ".")
    _run_git_command(workspace_root, "commit", "-m", "initial")
    (workspace_root / "src" / "main.py").write_text(
        "print('v2')\n",
        encoding="utf-8",
    )
    return workspace_root


def _run_git_command(workspace_root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        check=False,
        cwd=workspace_root,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Git command failed:\n"
            f"command={' '.join(args)}\n"
            f"stdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )
