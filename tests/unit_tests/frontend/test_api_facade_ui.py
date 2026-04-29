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


def test_core_api_facade_exports_session_terminal_view_helper() -> None:
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
                "console.log(typeof mod.markSessionTerminalRunViewed);"
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


def test_session_terminal_view_invalidates_session_cache(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "sessions.js"
    )
    module_under_test_path = tmp_path / "sessions.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequests.push({
        url,
        options,
        errorMessage,
    });
    return { status: 'ok' };
}

export async function requestJsonManaged(key, url, options, errorMessage) {
    return requestJson(url, options, errorMessage);
}

export function invalidateManagedRequests(prefix) {
    globalThis.__invalidatedPrefixes.push(prefix);
}

export function invalidateManagedRequestCache(prefix) {
    globalThis.__invalidatedCachePrefixes.push(prefix);
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.__capturedRequests = []; "
                "globalThis.__invalidatedPrefixes = []; "
                "globalThis.__invalidatedCachePrefixes = []; "
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "const result = await mod.markSessionTerminalRunViewed('session-a'); "
                "console.log(JSON.stringify({"
                "result,"
                "requests: globalThis.__capturedRequests,"
                "invalidatedPrefixes: globalThis.__invalidatedPrefixes,"
                "invalidatedCachePrefixes: globalThis.__invalidatedCachePrefixes"
                "}));"
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

    assert json.loads(completed.stdout.strip()) == {
        "result": {"status": "ok"},
        "requests": [
            {
                "url": "/api/sessions/session-a/terminal-view",
                "options": {"method": "POST"},
                "errorMessage": "Failed to mark session run viewed",
            }
        ],
        "invalidatedPrefixes": [],
        "invalidatedCachePrefixes": ["sessions:list", "sessions:session-a:record"],
    }


def test_fetch_sessions_force_refresh_invalidates_session_list_cache(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "sessions.js"
    )
    module_under_test_path = tmp_path / "sessions.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson() {
    throw new Error('not used');
}

export async function requestJsonManaged(key, url, options, errorMessage, config) {
    globalThis.__capturedManagedRequests.push({
        key,
        url,
        options,
        errorMessage,
        config,
    });
    return [{ session_id: 'session-a' }];
}

export function invalidateManagedRequests(prefix) {
    globalThis.__invalidatedPrefixes.push(prefix);
}

export function invalidateManagedRequestCache() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.__capturedManagedRequests = []; "
                "globalThis.__invalidatedPrefixes = []; "
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.fetchSessions(); "
                "await mod.fetchSessions({ forceRefresh: true }); "
                "console.log(JSON.stringify({"
                "managedRequests: globalThis.__capturedManagedRequests,"
                "invalidatedPrefixes: globalThis.__invalidatedPrefixes"
                "}));"
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
    assert payload["invalidatedPrefixes"] == ["sessions:list"]
    assert payload["managedRequests"] == [
        {
            "key": "sessions:list",
            "url": "/api/sessions",
            "options": {},
            "errorMessage": "Failed to fetch sessions",
            "config": {"ttlMs": 500},
        },
        {
            "key": "sessions:list",
            "url": "/api/sessions",
            "options": {},
            "errorMessage": "Failed to fetch sessions",
            "config": {"ttlMs": 500},
        },
    ]


def test_role_config_reads_use_managed_requests_and_writes_invalidate_cache(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "roles.js"
    module_under_test_path = tmp_path / "roles.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequests.push({
        url,
        options,
        errorMessage,
    });
    return { status: 'ok' };
}

export async function requestJsonManaged(key, url, options, errorMessage, config) {
    globalThis.__capturedManagedRequests.push({
        key,
        url,
        options,
        errorMessage,
        config,
    });
    return { status: 'ok' };
}

export function invalidateManagedRequests(prefix) {
    globalThis.__invalidatedPrefixes.push(prefix);
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.__capturedManagedRequests = []; "
                "globalThis.__capturedRequests = []; "
                "globalThis.__invalidatedPrefixes = []; "
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.fetchRoleConfigs(); "
                "await mod.fetchRoleConfigOptions(); "
                "await mod.saveRoleConfig('Writer', { role_id: 'Writer' }); "
                "await mod.deleteRoleConfig('Writer'); "
                "console.log(JSON.stringify({"
                "managedRequests: globalThis.__capturedManagedRequests,"
                "requests: globalThis.__capturedRequests,"
                "invalidatedPrefixes: globalThis.__invalidatedPrefixes"
                "}));"
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
    assert payload["managedRequests"] == [
        {
            "key": "roles:configs",
            "url": "/api/roles/configs",
            "options": {},
            "errorMessage": "Failed to fetch role configs",
            "config": {"ttlMs": 30000},
        },
        {
            "key": "roles:options",
            "url": "/api/roles:options",
            "options": {},
            "errorMessage": "Failed to fetch role options",
            "config": {"ttlMs": 30000},
        },
    ]
    assert payload["requests"][0]["url"] == "/api/roles/configs/Writer"
    assert payload["requests"][0]["options"]["method"] == "PUT"
    assert payload["requests"][1]["url"] == "/api/roles/configs/Writer"
    assert payload["requests"][1]["options"] == {"method": "DELETE"}
    assert payload["invalidatedPrefixes"] == ["roles:", "roles:"]


def test_orchestration_config_uses_managed_request_and_save_invalidates_cache(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "system.js"
    module_under_test_path = tmp_path / "system.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequests.push({
        url,
        options,
        errorMessage,
    });
    return { status: 'ok' };
}

export async function requestJsonManaged(key, url, options, errorMessage, config) {
    globalThis.__capturedManagedRequests.push({
        key,
        url,
        options,
        errorMessage,
        config,
    });
    return { status: 'ok' };
}

export function invalidateManagedRequests(prefix) {
    globalThis.__invalidatedPrefixes.push(prefix);
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.__capturedManagedRequests = []; "
                "globalThis.__capturedRequests = []; "
                "globalThis.__invalidatedPrefixes = []; "
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.fetchOrchestrationConfig(); "
                "await mod.saveOrchestrationConfig({ presets: [] }); "
                "console.log(JSON.stringify({"
                "managedRequests: globalThis.__capturedManagedRequests,"
                "requests: globalThis.__capturedRequests,"
                "invalidatedPrefixes: globalThis.__invalidatedPrefixes"
                "}));"
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
    assert payload["managedRequests"] == [
        {
            "key": "system:orchestration-config",
            "url": "/api/system/configs/orchestration",
            "options": {},
            "errorMessage": "Failed to fetch orchestration config",
            "config": {"ttlMs": 30000},
        }
    ]
    assert payload["requests"][0]["url"] == "/api/system/configs/orchestration"
    assert payload["requests"][0]["options"] == {
        "method": "PUT",
        "headers": {"Content-Type": "application/json"},
        "body": '{"config":{"presets":[]}}',
    }
    assert payload["invalidatedPrefixes"] == ["system:orchestration-config"]


def test_role_option_dependency_writes_invalidate_role_option_cache(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "system.js"
    module_under_test_path = tmp_path / "system.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequests.push({
        url,
        options,
        errorMessage,
    });
    return { status: 'ok' };
}

export async function requestJsonManaged(key, url, options, errorMessage, config) {
    globalThis.__capturedManagedRequests.push({
        key,
        url,
        options,
        errorMessage,
        config,
    });
    return { status: 'ok' };
}

export function invalidateManagedRequests(prefix) {
    globalThis.__invalidatedPrefixes.push(prefix);
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.__capturedManagedRequests = []; "
                "globalThis.__capturedRequests = []; "
                "globalThis.__invalidatedPrefixes = []; "
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.saveExternalAgent('local-agent', { name: 'Local Agent' }); "
                "await mod.deleteExternalAgent('local-agent'); "
                "await mod.saveModelProfile('vision-profile', { model: 'vision-model' }); "
                "await mod.deleteModelProfile('vision-profile'); "
                "await mod.saveClawHubSkill('writer', { name: 'Writer' }); "
                "await mod.deleteClawHubSkill('writer'); "
                "await mod.reloadMcpConfig(); "
                "await mod.addMcpServer({ name: 'filesystem' }); "
                "await mod.updateMcpServer('filesystem', { config: {} }); "
                "await mod.setMcpServerEnabled('filesystem', true); "
                "await mod.reloadSkillsConfig(); "
                "console.log(JSON.stringify({"
                "requests: globalThis.__capturedRequests,"
                "invalidatedPrefixes: globalThis.__invalidatedPrefixes"
                "}));"
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
    assert [request["url"] for request in payload["requests"]] == [
        "/api/system/configs/agents/local-agent",
        "/api/system/configs/agents/local-agent",
        "/api/system/configs/model/profiles/vision-profile",
        "/api/system/configs/model/profiles/vision-profile",
        "/api/system/configs/clawhub/skills/writer",
        "/api/system/configs/clawhub/skills/writer",
        "/api/system/configs/mcp:reload",
        "/api/mcp/servers",
        "/api/mcp/servers/filesystem",
        "/api/mcp/servers/filesystem/enabled",
        "/api/system/configs/skills:reload",
    ]
    assert payload["invalidatedPrefixes"] == [
        "roles:",
        "roles:",
        "system:model-profiles",
        "roles:",
        "system:model-profiles",
        "roles:",
        "roles:",
        "roles:",
        "roles:",
        "roles:",
        "roles:",
        "roles:",
        "roles:",
    ]


def test_fetch_session_rounds_supports_summary_query(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "sessions.js"
    )
    module_under_test_path = tmp_path / "sessions.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson() {
    throw new Error('not used');
}

export async function requestJsonManaged(key, url, options, errorMessage, config) {
    globalThis.__capturedManagedRequests.push({
        key,
        url,
        options,
        errorMessage,
        config,
    });
    return { items: [] };
}

export function invalidateManagedRequests() {
    return undefined;
}

export function invalidateManagedRequestCache() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    assert module_text != source_text
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.__capturedManagedRequests = []; "
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.fetchSessionRounds('session-a', {"
                "limit: 3, summary: true, priority: 'high'"
                "}); "
                "console.log(JSON.stringify(globalThis.__capturedManagedRequests));"
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

    assert json.loads(completed.stdout.strip()) == [
        {
            "key": "sessions:session-a:rounds:limit=3&summary=true",
            "url": "/api/sessions/session-a/rounds?limit=3&summary=true",
            "options": {},
            "errorMessage": "Failed to fetch session rounds",
            "config": {"lane": "critical", "priority": "high", "ttlMs": 300},
        }
    ]


def test_core_api_facade_exports_legacy_dispatch_human_task_alias() -> None:
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
                "console.log(typeof mod.dispatchHumanTask);"
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


def test_core_api_facade_exports_workspace_provider_helpers() -> None:
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
                "console.log(["
                "typeof mod.updateWorkspace,"
                "typeof mod.fetchSshProfiles,"
                "typeof mod.saveSshProfile,"
                "typeof mod.revealSshProfilePassword,"
                "typeof mod.probeSshProfileConnection,"
                "typeof mod.deleteSshProfile"
                "].join(','));"
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

export async function requestJsonManaged(key, url, options, errorMessage) {
    return requestJson(url, options, errorMessage);
}

export function invalidateManagedRequests() {}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
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

export async function requestJsonManaged(key, url, options, errorMessage) {
    return requestJson(url, options, errorMessage);
}

export function invalidateManagedRequests() {}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
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


def test_workspace_update_posts_mount_payload(
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
        body: JSON.parse(options.body),
    };
    return globalThis.__capturedRequest;
}

export async function requestJsonManaged(key, url, options, errorMessage) {
    return requestJson(url, options, errorMessage);
}

export function invalidateManagedRequests() {}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.updateWorkspace('project-alpha', { "
                "default_mount_name: 'ops', "
                "mounts: [{ mount_name: 'ops', provider: 'ssh', provider_config: { ssh_profile_id: 'prod', remote_root: '/srv/app' } }] "
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
    assert payload == {
        "url": "/api/workspaces/project-alpha",
        "options": {
            "method": "PUT",
            "headers": {"Content-Type": "application/json"},
            "body": '{"default_mount_name":"ops","mounts":[{"mount_name":"ops","provider":"ssh","provider_config":{"ssh_profile_id":"prod","remote_root":"/srv/app"}}]}',
        },
        "errorMessage": "Failed to update project workspace",
        "body": {
            "default_mount_name": "ops",
            "mounts": [
                {
                    "mount_name": "ops",
                    "provider": "ssh",
                    "provider_config": {
                        "ssh_profile_id": "prod",
                        "remote_root": "/srv/app",
                    },
                }
            ],
        },
    }


def test_ssh_profile_api_helpers_call_expected_endpoints(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "system.js"
    module_under_test_path = tmp_path / "system.mjs"
    mock_request_path = tmp_path / "mockRequest.mjs"

    mock_request_path.write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__capturedRequests = globalThis.__capturedRequests || [];
    globalThis.__capturedRequests.push({
        url,
        options: options ?? null,
        errorMessage,
        body: options?.body ? JSON.parse(options.body) : null,
    });
    return globalThis.__capturedRequests.at(-1);
}

export async function requestJsonManaged(key, url, options, errorMessage) {
    return requestJson(url, options, errorMessage);
}

export function invalidateManagedRequests() {}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "await mod.fetchSshProfiles(); "
                "await mod.saveSshProfile('prod', { host: 'prod-alias', username: 'deploy', password: 'secret', private_key: 'KEY', private_key_name: 'id_ed25519' }); "
                "await mod.revealSshProfilePassword('prod'); "
                "await mod.probeSshProfileConnection({ ssh_profile_id: 'prod' }); "
                "await mod.deleteSshProfile('prod'); "
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
            "url": "/api/system/configs/workspace/ssh-profiles",
            "options": None,
            "errorMessage": "Failed to fetch SSH profiles",
            "body": None,
        },
        {
            "url": "/api/system/configs/workspace/ssh-profiles/prod",
            "options": {
                "method": "PUT",
                "headers": {"Content-Type": "application/json"},
                "body": '{"config":{"host":"prod-alias","username":"deploy","password":"secret","private_key":"KEY","private_key_name":"id_ed25519"}}',
            },
            "errorMessage": "Failed to save SSH profile",
            "body": {
                "config": {
                    "host": "prod-alias",
                    "username": "deploy",
                    "password": "secret",
                    "private_key": "KEY",
                    "private_key_name": "id_ed25519",
                }
            },
        },
        {
            "url": "/api/system/configs/workspace/ssh-profiles/prod:reveal-password",
            "options": {"method": "POST"},
            "errorMessage": "Failed to reveal SSH profile password",
            "body": None,
        },
        {
            "url": "/api/system/configs/workspace/ssh-profiles:probe",
            "options": {
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": '{"ssh_profile_id":"prod"}',
            },
            "errorMessage": "Failed to test SSH profile",
            "body": {"ssh_profile_id": "prod"},
        },
        {
            "url": "/api/system/configs/workspace/ssh-profiles/prod",
            "options": {"method": "DELETE"},
            "errorMessage": "Failed to delete SSH profile",
            "body": None,
        },
    ]


def test_model_catalog_api_helpers_call_expected_endpoints(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "system.js"
    module_under_test_path = tmp_path / "system.mjs"
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

export async function requestJsonManaged(key, url, options, errorMessage) {
    return requestJson(url, options, errorMessage);
}

export function invalidateManagedRequests() {}
""".strip(),
        encoding="utf-8",
    )

    source_text = source_path.read_text(encoding="utf-8")
    module_text = source_text.replace(
        "from './request.js';",
        "from './mockRequest.mjs';",
    )
    module_under_test_path.write_text(module_text, encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                f"const mod = await import({module_under_test_path.as_uri()!r}); "
                "const signal = {}; "
                "await mod.fetchModelCatalog({ signal }); "
                "await mod.fetchModelCatalog({ refresh: true }); "
                "await mod.refreshModelCatalog(); "
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
            "url": "/api/system/configs/model/catalog",
            "options": {"signal": {}},
            "errorMessage": "Failed to fetch model catalog",
        },
        {
            "url": "/api/system/configs/model/catalog?refresh=true",
            "options": {},
            "errorMessage": "Failed to fetch model catalog",
        },
        {
            "url": "/api/system/configs/model/catalog:refresh",
            "options": {"method": "POST"},
            "errorMessage": "Failed to refresh model catalog",
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


def test_request_json_formats_structured_detail_arrays(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    (tmp_path / "mockBackendStatus.mjs").write_text(
        """
export function markBackendOffline() {}
export function markBackendOnline() {}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { message: error?.message || '', ...extra };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../utils/backendStatus.js", "./mockBackendStatus.mjs")
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
                "globalThis.fetch = async () => ({ "
                "ok: false, "
                "status: 400, "
                "async json() { return { detail: [{ loc: ['hooks', 'PreToolUse', 0, 'hooks', 0, 'command'], msg: 'Field required' }] }; } "
                "}); "
                "try { "
                "await mod.requestJson('/api/system/configs/hooks:validate', { method: 'POST' }, 'validate failed'); "
                "} catch (error) { "
                "console.log(JSON.stringify({ message: error.message, detail: error.detail })); "
                "}"
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
        "message": "hooks.PreToolUse.0.hooks.0.command: Field required",
        "detail": "hooks.PreToolUse.0.hooks.0.command: Field required",
    }
