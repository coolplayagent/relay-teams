from __future__ import annotations

import json
from pathlib import Path

import subprocess


def test_core_api_facade_exports_update_session() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log(typeof mod.updateSession);"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function"


def test_core_api_facade_exports_ui_language_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log([typeof mod.fetchUiLanguageSettings, typeof mod.saveUiLanguageSettings].join(','));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function,function"


def test_core_api_facade_exports_trigger_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log([typeof mod.fetchTriggers, typeof mod.createTrigger, typeof mod.updateTrigger, typeof mod.enableTrigger, typeof mod.disableTrigger, typeof mod.rotateTriggerToken].join(','));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert (
        completed.stdout.strip()
        == "function,function,function,function,function,function"
    )


def test_update_trigger_omits_enabled_field_from_patch_payload(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "triggers.js"
    )
    module_under_test_path = tmp_path / "triggers.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequest = {
        url,
        options,
        errorMessage,
        body: JSON.parse(options.body),
    };
    return globalThis.__capturedRequest;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "import { requestJson } from './request.js';",
        "import { requestJson } from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.updateTrigger('trg_demo', {"
                "name: 'feishu_main', "
                "display_name: null, "
                "source_config: {"
                "provider: 'feishu', "
                "trigger_rule: 'mention_only', "
                "app_id: 'cli_demo', "
                "app_name: 'Agent Teams Bot'"
                "}, "
                "target_config: { workspace_id: 'default' }"
                "}); "
                "console.log(JSON.stringify(globalThis.__capturedRequest));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout.strip())
    assert payload["url"] == "/api/gateway/feishu/accounts/trg_demo"
    assert payload["options"]["method"] == "PATCH"
    assert payload["errorMessage"] == "Failed to update Feishu gateway account"
    assert payload["body"] == {
        "name": "feishu_main",
        "display_name": None,
        "source_config": {
            "provider": "feishu",
            "trigger_rule": "mention_only",
            "app_id": "cli_demo",
            "app_name": "Agent Teams Bot",
        },
        "target_config": {"workspace_id": "default"},
    }


def test_core_api_facade_exports_wechat_gateway_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log([typeof mod.fetchWeChatGatewayAccounts, typeof mod.startWeChatGatewayLogin, typeof mod.waitWeChatGatewayLogin, typeof mod.updateWeChatGatewayAccount, typeof mod.enableWeChatGatewayAccount, typeof mod.disableWeChatGatewayAccount, typeof mod.deleteWeChatGatewayAccount].join(','));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert (
        completed.stdout.strip()
        == "function,function,function,function,function,function,function"
    )


def test_core_api_facade_exports_legacy_open_workspace_alias() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log([typeof mod.openWorkspace, typeof mod.pickWorkspace, mod.openWorkspace === mod.pickWorkspace].join(','));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function,function,true"


def test_core_api_facade_exports_open_workspace_root() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log(typeof mod.openWorkspaceRoot);"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function"


def test_open_workspace_root_posts_expected_endpoint(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "workspaces.js"
    )
    module_under_test_path = tmp_path / "workspaces.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequest = {
        url,
        options,
        errorMessage,
    };
    return globalThis.__capturedRequest;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "import { requestJson } from './request.js';",
        "import { requestJson } from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.openWorkspaceRoot('project-alpha'); "
                "console.log(JSON.stringify(globalThis.__capturedRequest));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout.strip())
    assert payload == {
        "url": "/api/workspaces/project-alpha:open-root",
        "options": {"method": "POST"},
        "errorMessage": "Failed to open project folder",
    }


def test_workspace_api_helpers_support_mount_query_parameters(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "workspaces.js"
    )
    module_under_test_path = tmp_path / "workspaces.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequests = globalThis.__capturedRequests || [];
    globalThis.__capturedRequests.push({
        url,
        options: options ?? null,
        errorMessage,
    });
    return globalThis.__capturedRequests.at(-1);
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "import { requestJson } from './request.js';",
        "import { requestJson } from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.openWorkspaceRoot('project-alpha', 'ops'); "
                "await mod.fetchWorkspaceTree('project-alpha', 'src', 'ops'); "
                "await mod.fetchWorkspaceDiffs('project-alpha', 'ops'); "
                "await mod.fetchWorkspaceDiffFile('project-alpha', 'src/main.py', 'ops'); "
                "console.log(JSON.stringify(globalThis.__capturedRequests));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout.strip())
    assert payload == [
        {
            "url": "/api/workspaces/project-alpha:open-root?mount=ops",
            "options": {"method": "POST"},
            "errorMessage": "Failed to open project folder",
        },
        {
            "url": "/api/workspaces/project-alpha/tree?path=src&mount=ops",
            "options": None,
            "errorMessage": "Failed to fetch project workspace tree",
        },
        {
            "url": "/api/workspaces/project-alpha/diffs?mount=ops",
            "options": None,
            "errorMessage": "Failed to fetch project workspace diffs",
        },
        {
            "url": "/api/workspaces/project-alpha/diff?path=src%2Fmain.py&mount=ops",
            "options": None,
            "errorMessage": "Failed to fetch project workspace diff file",
        },
    ]


def test_request_json_disables_browser_cache_for_get_requests(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    backend_status_path = tmp_path / "mockBackendStatus.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    backend_status_path.write_text(
        """
export function markBackendOffline() {
    return undefined;
}

export function markBackendOnline() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    logger_path.write_text(
        """
export function errorToPayload(error, extra = {}) {
    return {
        error_message: String(error?.message || error || ""),
        ...extra,
    };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace(
            "../../utils/backendStatus.js",
            "./mockBackendStatus.mjs",
        )
        .replace("../../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "globalThis.__calls = []; "
                "globalThis.fetch = async (url, options) => { "
                "globalThis.__calls.push({ url, options }); "
                "return { ok: true, async json() { return { ok: true }; } }; "
                "}; "
                "await mod.requestJson('/api/roles:options', undefined, 'load failed'); "
                "await mod.requestJson('/api/roles/configs/test', { method: 'GET' }, 'load failed'); "
                "await mod.requestJson('/api/roles/configs/test', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' }, 'save failed'); "
                "console.log(JSON.stringify(globalThis.__calls));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout.strip())
    assert payload[0] == {
        "url": "/api/roles:options",
        "options": {"cache": "no-store"},
    }
    assert payload[1] == {
        "url": "/api/roles/configs/test",
        "options": {"method": "GET", "cache": "no-store"},
    }
    assert payload[2]["url"] == "/api/roles/configs/test"
    assert payload[2]["options"] == {
        "method": "PUT",
        "headers": {"Content-Type": "application/json"},
        "body": "{}",
    }
