# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast

from .css_helpers import load_components_css


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
    proxyPasswordValue: document.getElementById("proxy-password").value,
    proxyPasswordPlaceholder: document.getElementById("proxy-password").placeholder,
    proxyPasswordType: document.getElementById("proxy-password").type,
    toggleDisplay: document.getElementById("toggle-proxy-password-btn").style.display,
    sslVerify: document.getElementById("proxy-ssl-verify").value,
}));
""".strip(),
    )

    assert payload["notifications"] == []
    assert payload["httpProxy"] == "http://proxy.example:8080"
    assert payload["httpsProxy"] == "http://proxy.example:8443"
    assert payload["allProxy"] == ""
    assert payload["noProxy"] == "localhost,127.0.0.1"
    assert payload["proxyUsername"] == "alice"
    assert payload["proxyPasswordValue"] == ""
    assert payload["proxyPasswordPlaceholder"] == "************"
    assert payload["proxyPasswordType"] == "password"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["sslVerify"] == "true"


def test_proxy_settings_panel_defaults_ssl_verify_to_skip_verify(
    tmp_path: Path,
) -> None:
    payload = _run_proxy_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
            "no_proxy": "",
            "proxy_username": "",
            "proxy_password": "",
            "ssl_verify": None,
        },
        runner_source="""
import { loadProxyStatusPanel } from "./proxySettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

await loadProxyStatusPanel();

console.log(JSON.stringify({
    notifications,
    sslVerify: document.getElementById("proxy-ssl-verify").value,
}));
""".strip(),
    )

    assert payload["notifications"] == []
    assert payload["sslVerify"] == "false"


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
document.getElementById("proxy-password").value = "edited-secret";
document.getElementById("proxy-password").oninput();
document.getElementById("proxy-no-proxy").value = "localhost,127.0.0.1,.internal";
document.getElementById("proxy-ssl-verify").value = "false";
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

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    assert probe_payload == {
        "url": "https://example.com",
        "timeout_ms": 2500,
        "proxy_override": {
            "http_proxy": "http://edited.example:8080",
            "https_proxy": "http://edited.example:8443",
            "all_proxy": "",
            "no_proxy": "localhost,127.0.0.1,.internal",
            "proxy_username": "alice",
            "proxy_password": "edited-secret",
            "ssl_verify": False,
        },
    }
    assert payload["savePayload"] == {
        "http_proxy": "http://edited.example:8080",
        "https_proxy": "http://edited.example:8443",
        "all_proxy": "",
        "no_proxy": "localhost,127.0.0.1,.internal",
        "proxy_username": "alice",
        "proxy_password": "edited-secret",
        "ssl_verify": False,
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


def test_proxy_probe_and_save_preserve_saved_password_when_left_unchanged(
    tmp_path: Path,
) -> None:
    payload = _run_proxy_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindProxySettingsHandlers, loadProxyStatusPanel } from "./proxySettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindProxySettingsHandlers();
await loadProxyStatusPanel();

document.getElementById("toggle-proxy-password-btn").onclick();
const revealedValue = document.getElementById("proxy-password").value;
const revealedType = document.getElementById("proxy-password").type;
const toggleTitle = document.getElementById("toggle-proxy-password-btn").title;
document.getElementById("toggle-proxy-password-btn").onclick();
document.getElementById("proxy-probe-url").value = "https://example.com";

await document.getElementById("test-proxy-web-btn").onclick();
await document.getElementById("save-proxy-btn").onclick();

console.log(JSON.stringify({
    notifications,
    revealedValue,
    revealedType,
    toggleTitle,
    savePayload: globalThis.__saveProxyPayload,
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    save_payload = cast(dict[str, JsonValue], payload["savePayload"])
    proxy_override = cast(dict[str, JsonValue], probe_payload["proxy_override"])
    assert payload["revealedValue"] == "secret"
    assert payload["revealedType"] == "text"
    assert payload["toggleTitle"] == "Hide password"
    assert proxy_override["proxy_password"] == "secret"
    assert save_payload["proxy_password"] == "secret"
    assert notifications == [
        {
            "title": "Proxy Saved",
            "message": "Proxy settings saved and reloaded.",
            "tone": "success",
        }
    ]


def test_proxy_settings_ignore_unfocused_autofilled_saved_password(
    tmp_path: Path,
) -> None:
    payload = _run_proxy_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindProxySettingsHandlers, loadProxyStatusPanel } from "./proxySettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindProxySettingsHandlers();
await loadProxyStatusPanel();

document.activeElement = null;
document.getElementById("proxy-password").value = "browser_password";
document.getElementById("proxy-password").oninput();
document.getElementById("proxy-probe-url").value = "https://example.com";

await document.getElementById("test-proxy-web-btn").onclick();
await document.getElementById("save-proxy-btn").onclick();

console.log(JSON.stringify({
    proxyPasswordValue: document.getElementById("proxy-password").value,
    proxyPasswordPlaceholder: document.getElementById("proxy-password").placeholder,
    savePayload: globalThis.__saveProxyPayload,
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    save_payload = cast(dict[str, JsonValue], payload["savePayload"])
    proxy_override = cast(dict[str, JsonValue], probe_payload["proxy_override"])
    assert payload["proxyPasswordValue"] == ""
    assert payload["proxyPasswordPlaceholder"] == "************"
    assert proxy_override["proxy_password"] == "secret"
    assert save_payload["proxy_password"] == "secret"


def test_proxy_settings_panel_allows_clearing_saved_password(
    tmp_path: Path,
) -> None:
    payload = _run_proxy_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindProxySettingsHandlers, loadProxyStatusPanel } from "./proxySettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindProxySettingsHandlers();
await loadProxyStatusPanel();

document.getElementById("toggle-proxy-password-btn").onclick();
document.getElementById("proxy-password").value = "";
document.getElementById("proxy-password").oninput();
document.getElementById("proxy-probe-url").value = "https://example.com";

await document.getElementById("test-proxy-web-btn").onclick();
await document.getElementById("save-proxy-btn").onclick();

console.log(JSON.stringify({
    notifications,
    savePayload: globalThis.__saveProxyPayload,
    probePayload: globalThis.__probePayload,
    proxyPasswordValue: document.getElementById("proxy-password").value,
    proxyPasswordPlaceholder: document.getElementById("proxy-password").placeholder,
    toggleDisplay: document.getElementById("toggle-proxy-password-btn").style.display,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    save_payload = cast(dict[str, JsonValue], payload["savePayload"])
    proxy_override = cast(dict[str, JsonValue], probe_payload["proxy_override"])
    assert proxy_override["proxy_password"] is None
    assert save_payload["proxy_password"] is None
    assert payload["proxyPasswordValue"] == ""
    assert payload["proxyPasswordPlaceholder"] == "Optional proxy password"
    assert payload["toggleDisplay"] == "none"
    assert notifications == [
        {
            "title": "Proxy Saved",
            "message": "Proxy settings saved and reloaded.",
            "tone": "success",
        }
    ]


def test_proxy_settings_styles_keep_a_single_editor_surface() -> None:
    components_css = load_components_css()

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
    assert "grid-template-columns: 128px minmax(0, 1fr) 128px;" in test_row_rule
    assert "min-width: 128px;" in test_btn_rule
    assert "min-height: 42px;" in test_btn_rule
    assert "grid-column: 1 / -1;" in probe_rule
    assert "border-radius: 6px;" in probe_rule


def _run_proxy_settings_script(
    tmp_path: Path,
    runner_source: str,
    *,
    fetch_config: dict[str, JsonValue] | None = None,
) -> dict[str, object]:
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
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "proxySettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    fetch_proxy_config = fetch_config or {
        "http_proxy": "http://proxy.example:8080",
        "https_proxy": "http://proxy.example:8443",
        "all_proxy": "",
        "no_proxy": "localhost,127.0.0.1",
        "proxy_username": "alice",
        "proxy_password": "secret",
        "ssl_verify": True,
    }
    fetch_proxy_config_json = json.dumps(fetch_proxy_config)

    mock_api_path.write_text(
        """
let currentConfig = __FETCH_PROXY_CONFIG__;

export async function fetchProxyConfig() {
    return currentConfig;
}

export async function saveProxyConfig(payload) {
    globalThis.__saveProxyCalls += 1;
    globalThis.__saveProxyPayload = payload;
    currentConfig = payload;
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
""".replace("__FETCH_PROXY_CONFIG__", fetch_proxy_config_json).strip(),
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
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.proxy.load_failed": "Load Failed",
    "settings.proxy.load_failed_detail": "Failed to load proxy settings: {error}",
    "settings.proxy.saved": "Proxy Saved",
    "settings.proxy.save_failed": "Save Failed",
    "settings.proxy.save_failed_detail": "Failed to save proxy settings: {error}",
    "settings.proxy.saved_message": "Proxy settings saved and reloaded.",
    "settings.proxy.enter_url": "Enter a target URL before testing connectivity.",
    "settings.proxy.testing_message": "Testing connectivity...",
    "settings.proxy.probe_failed": "Proxy probe failed: {error}",
    "settings.proxy.probe_success": "{method} {status_code} in {latency_ms}ms",
    "settings.proxy.probe_reason": "{status_text}: {reason}",
    "settings.proxy.test_url": "Test URL",
    "settings.proxy.testing": "Testing...",
    "settings.proxy.show_password": "Show password",
    "settings.proxy.hide_password": "Hide password",
    "settings.proxy.password_placeholder": "Optional proxy password",
};

export function t(key) {
    return translations[key] || key;
}

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
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
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
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
        ["toggle-proxy-password-btn", createElement("none")],
        ["proxy-no-proxy", createElement("block")],
        ["proxy-ssl-verify", createElement("block")],
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
