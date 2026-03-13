# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from agent_teams.shared_types.json_types import JsonObject


def test_proxy_settings_panel_loads_saved_values_into_form(tmp_path: Path) -> None:
    payload = _run_proxy_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadProxyStatusPanel } from "./proxySettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

await loadProxyStatusPanel();

console.log(JSON.stringify({
    notifications,
    httpProxy: document.getElementById("proxy-http-proxy").value,
    httpsProxy: document.getElementById("proxy-https-proxy").value,
    allProxy: document.getElementById("proxy-all-proxy").value,
    noProxy: document.getElementById("proxy-no-proxy").value,
    proxyUsername: document.getElementById("proxy-username").value,
    proxyPassword: document.getElementById("proxy-password").value,
    verifySsl: document.getElementById("proxy-verify-ssl").checked,
}));
""".strip(),
    )

    assert payload["notifications"] == []
    assert payload["httpProxy"] == "http://proxy.example:8080"
    assert payload["httpsProxy"] == "http://proxy.example:8443"
    assert payload["allProxy"] == ""
    assert payload["noProxy"] == "localhost,127.0.0.1"
    assert payload["proxyUsername"] == "alice"
    assert payload["proxyPassword"] == "secret"
    assert payload["verifySsl"] is True


def test_proxy_probe_and_save_actions_use_current_inputs(tmp_path: Path) -> None:
    payload = _run_proxy_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindProxySettingsHandlers, loadProxyStatusPanel } from "./proxySettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindProxySettingsHandlers();
await loadProxyStatusPanel();

document.getElementById("proxy-http-proxy").value = "http://edited.example:8080";
document.getElementById("proxy-https-proxy").value = "http://edited.example:8443";
document.getElementById("proxy-username").value = "alice";
document.getElementById("proxy-password").value = "secret";
document.getElementById("proxy-no-proxy").value = "localhost,127.0.0.1,.internal";
document.getElementById("proxy-verify-ssl").checked = false;
document.getElementById("proxy-probe-url").value = "https://example.com";
document.getElementById("proxy-probe-timeout").value = "2500";

await document.getElementById("test-proxy-web-btn").onclick();
await document.getElementById("save-proxy-btn").onclick();

console.log(JSON.stringify({
    notifications,
    probePayload: globalThis.__probePayload,
    savePayload: globalThis.__saveProxyPayload,
    saveCalls: globalThis.__saveProxyCalls,
    probeStatusText: document.getElementById("proxy-probe-status").textContent,
    probeStatusDisplay: document.getElementById("proxy-probe-status").style.display,
    probeButtonText: document.getElementById("test-proxy-web-btn").textContent,
}));
""".strip(),
    )

    notifications = cast(list[JsonObject], payload["notifications"])
    probe_payload = cast(JsonObject, payload["probePayload"])
    assert probe_payload == {
        "url": "https://example.com",
        "timeout_ms": 2500,
        "proxy_override": {
            "http_proxy": "http://edited.example:8080",
            "https_proxy": "http://edited.example:8443",
            "all_proxy": "",
            "no_proxy": "localhost,127.0.0.1,.internal",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "verify_ssl": False,
        },
    }
    assert payload["savePayload"] == {
        "http_proxy": "http://edited.example:8080",
        "https_proxy": "http://edited.example:8443",
        "all_proxy": "",
        "no_proxy": "localhost,127.0.0.1,.internal",
        "proxy_username": "alice",
        "proxy_password": "secret",
        "verify_ssl": False,
    }
    assert payload["saveCalls"] == 1
    assert payload["probeStatusDisplay"] == "block"
    assert "HEAD 200 in 38ms" in cast(str, payload["probeStatusText"])
    assert payload["probeButtonText"] == "Test URL"
    assert notifications == [
        {
            "title": "Proxy Saved",
            "message": "Proxy settings saved and reloaded.",
            "tone": "success",
        }
    ]


def test_proxy_settings_styles_keep_a_single_editor_surface() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    panel_start = components_css.index(".proxy-panel-body {")
    panel_end = components_css.index(".proxy-editor-form {", panel_start)
    panel_rule = components_css[panel_start:panel_end]

    editor_start = components_css.index(".proxy-editor-form {")
    editor_end = components_css.index(".proxy-form-section {", editor_start)
    editor_rule = components_css[editor_start:editor_end]

    section_start = components_css.index(".proxy-form-section {", editor_end)
    section_end = components_css.index(
        ".proxy-form-section:first-child {", section_start
    )
    section_rule = components_css[section_start:section_end]

    split_start = components_css.index(".proxy-form-section + .proxy-form-section {")
    split_end = components_css.index(".proxy-form-section-header {", split_start)
    split_rule = components_css[split_start:split_end]

    input_start = components_css.index(".proxy-inline-field input {")
    input_end = components_css.index(".proxy-inline-field-test {", input_start)
    input_rule = components_css[input_start:input_end]

    test_row_start = components_css.index(".proxy-inline-field-test {")
    test_row_end = components_css.index(".proxy-inline-field-compact {", test_row_start)
    test_row_rule = components_css[test_row_start:test_row_end]

    test_btn_start = components_css.index(".proxy-inline-test-btn {")
    test_btn_end = components_css.index(".proxy-probe-status {", test_btn_start)
    test_btn_rule = components_css[test_btn_start:test_btn_end]

    probe_start = components_css.index(".proxy-probe-status {")
    probe_end = components_css.index(".notifications-help {", probe_start)
    probe_rule = components_css[probe_start:probe_end]

    assert "gap: 0;" in panel_rule
    assert "min-width: 0;" in editor_rule
    assert "padding: 1rem 0;" in section_rule
    assert "border-top: 1px solid var(--settings-divider);" in section_rule
    assert "margin-top: 0;" in split_rule
    assert "border-radius: 6px;" in input_rule
    assert "grid-template-columns: 128px minmax(0, 1fr) auto;" in test_row_rule
    assert "min-width: 92px;" in test_btn_rule
    assert "grid-column: 1 / -1;" in probe_rule
    assert "border-radius: 6px;" in probe_rule


def _run_proxy_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "proxySettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "proxySettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchProxyConfig() {
    return {
        http_proxy: "http://proxy.example:8080",
        https_proxy: "http://proxy.example:8443",
        all_proxy: "",
        no_proxy: "localhost,127.0.0.1",
        proxy_username: "alice",
        proxy_password: "secret",
        verify_ssl: true,
    };
}

export async function saveProxyConfig(payload) {
    globalThis.__saveProxyCalls += 1;
    globalThis.__saveProxyPayload = payload;
    return { status: "ok" };
}

export async function probeWebConnectivity(payload) {
    globalThis.__probePayload = payload;
    return {
        ok: true,
        status_code: 200,
        latency_ms: 38,
        used_method: "HEAD",
        diagnostics: {
            used_proxy: true,
            redirected: false,
        },
    };
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export function showToast(payload) {
    globalThis.__feedbackNotifications.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_logger_path.write_text(
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
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createElement(initialDisplay = "block") {{
    return {{
        style: {{ display: initialDisplay }},
        value: "",
        disabled: false,
        textContent: "",
        innerHTML: "",
        className: "",
        onclick: null,
    }};
}}

function createElements() {{
    return new Map([
        ["proxy-http-proxy", createElement("block")],
        ["proxy-https-proxy", createElement("block")],
        ["proxy-all-proxy", createElement("block")],
        ["proxy-username", createElement("block")],
        ["proxy-password", createElement("block")],
        ["proxy-no-proxy", createElement("block")],
        ["proxy-verify-ssl", (() => {{
            const element = createElement("block");
            element.checked = true;
            return element;
        }})()],
        ["proxy-probe-url", createElement("block")],
        ["proxy-probe-timeout", createElement("block")],
        ["proxy-probe-status", createElement("none")],
        ["save-proxy-btn", createElement("block")],
        ["test-proxy-web-btn", createElement("block")],
    ]);
}}

function installGlobals(elements, notifications) {{
    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
    }};
    globalThis.__feedbackNotifications = notifications;
    globalThis.__saveProxyCalls = 0;
    globalThis.__saveProxyPayload = null;
    globalThis.__probePayload = null;
}}

{runner_source}
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
