# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast


def test_github_settings_panel_loads_saves_and_probes(tmp_path: Path) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();

document.getElementById("github-token").value = "ghp_secret";
document.getElementById("github-token").oninput();
document.getElementById("github-webhook-base-url").value = "https://agent-teams.example.com/app";
document.getElementById("github-webhook-base-url").oninput();

await document.getElementById("test-github-btn").onclick();
await document.getElementById("save-github-btn").onclick();
await document.getElementById("test-github-webhook-btn").onclick();
await document.getElementById("save-github-webhook-btn").onclick();

console.log(JSON.stringify({
    notifications,
    tokenValue: document.getElementById("github-token").value,
    tokenPlaceholder: document.getElementById("github-token").placeholder,
    tokenType: document.getElementById("github-token").type,
    toggleDisplay: document.getElementById("toggle-github-token-btn").style.display,
    webhookBaseUrlValue: document.getElementById("github-webhook-base-url").value,
    callbackPreviewText: document.getElementById("github-callback-preview").textContent,
    savePayloads: globalThis.__saveGitHubPayloads,
    probePayload: globalThis.__probeGitHubPayload,
    webhookProbePayload: globalThis.__probeGitHubWebhookPayload,
    probeStatusText: document.getElementById("github-probe-status").textContent,
    probeStatusDisplay: document.getElementById("github-probe-status").style.display,
    webhookProbeStatusText: document.getElementById("github-webhook-probe-status").textContent,
    webhookProbeStatusDisplay: document.getElementById("github-webhook-probe-status").style.display,
    probeButtonText: document.getElementById("test-github-btn").textContent,
    webhookProbeButtonText: document.getElementById("test-github-webhook-btn").textContent,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "************"
    assert payload["tokenType"] == "password"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["webhookBaseUrlValue"] == "https://agent-teams.example.com/app"
    assert (
        payload["callbackPreviewText"]
        == "https://agent-teams.example.com/app/api/triggers/github/deliveries"
    )
    assert payload["savePayloads"] == [
        {"token": "ghp_secret"},
        {"webhook_base_url": "https://agent-teams.example.com/app"},
    ]
    assert payload["probePayload"] == {"token": "ghp_secret"}
    assert payload["webhookProbePayload"] == {
        "webhook_base_url": "https://agent-teams.example.com/app"
    }
    assert payload["probeStatusDisplay"] == "block"
    assert "octocat via gh 2.88.1 in 42ms" in cast(str, payload["probeStatusText"])
    assert payload["webhookProbeStatusDisplay"] == "block"
    assert (
        "Webhook reachability OK: 200 in 44ms via "
        "https://agent-teams.example.com/app/api/system/health"
    ) in cast(str, payload["webhookProbeStatusText"])
    assert payload["probeButtonText"] == "Test Connection"
    assert payload["webhookProbeButtonText"] == "Test Callback URL"
    assert notifications == [
        {
            "title": "GitHub Settings Saved",
            "message": "GitHub settings saved.",
            "tone": "success",
        },
        {
            "title": "GitHub Webhook Saved",
            "message": "GitHub webhook settings saved.",
            "tone": "success",
        },
    ]


def test_github_settings_panel_preserves_saved_token_for_probe_and_save(
    tmp_path: Path,
) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "token": "ghp_saved",
            "token_configured": True,
            "webhook_base_url": "https://agent-teams.example.com/app",
        },
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();

await document.getElementById("toggle-github-token-btn").onclick();
const revealedValue = document.getElementById("github-token").value;
const revealedType = document.getElementById("github-token").type;
const toggleTitle = document.getElementById("toggle-github-token-btn").title;
await document.getElementById("toggle-github-token-btn").onclick();

await document.getElementById("test-github-btn").onclick();
await document.getElementById("save-github-btn").onclick();
await document.getElementById("test-github-webhook-btn").onclick();
await document.getElementById("save-github-webhook-btn").onclick();

console.log(JSON.stringify({
    revealedValue,
    revealedType,
    toggleTitle,
    revealCalls: globalThis.__revealGitHubTokenCalls,
    tokenValue: document.getElementById("github-token").value,
    tokenPlaceholder: document.getElementById("github-token").placeholder,
    toggleDisplay: document.getElementById("toggle-github-token-btn").style.display,
    webhookBaseUrlValue: document.getElementById("github-webhook-base-url").value,
    callbackPreviewText: document.getElementById("github-callback-preview").textContent,
    savePayloads: globalThis.__saveGitHubPayloads,
    probePayload: globalThis.__probeGitHubPayload,
    webhookProbePayload: globalThis.__probeGitHubWebhookPayload,
}));
""".strip(),
    )

    assert payload["revealedValue"] == "ghp_saved"
    assert payload["revealedType"] == "text"
    assert payload["toggleTitle"] == "Hide GitHub token"
    assert payload["revealCalls"] == 1
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "************"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["webhookBaseUrlValue"] == "https://agent-teams.example.com/app"
    assert (
        payload["callbackPreviewText"]
        == "https://agent-teams.example.com/app/api/triggers/github/deliveries"
    )
    assert payload["savePayloads"] == [
        {},
        {"webhook_base_url": "https://agent-teams.example.com/app"},
    ]
    assert payload["probePayload"] == {}
    assert payload["webhookProbePayload"] == {
        "webhook_base_url": "https://agent-teams.example.com/app"
    }


def test_github_settings_panel_starts_and_stops_temporary_public_url(
    tmp_path: Path,
) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();
await document.getElementById("start-github-webhook-tunnel-btn").onclick();

const afterStart = {
    webhookBaseUrlValue: document.getElementById("github-webhook-base-url").value,
    callbackPreviewText: document.getElementById("github-callback-preview").textContent,
    tunnelStatusText: document.getElementById("github-webhook-tunnel-status").textContent,
};

await document.getElementById("stop-github-webhook-tunnel-btn").onclick();

console.log(JSON.stringify({
    notifications,
    startTunnelPayload: globalThis.__startTunnelPayload,
    stopTunnelPayload: globalThis.__stopTunnelPayload,
    afterStart,
    afterStopWebhookBaseUrlValue: document.getElementById("github-webhook-base-url").value,
    afterStopCallbackPreviewText: document.getElementById("github-callback-preview").textContent,
    afterStopTunnelStatusText: document.getElementById("github-webhook-tunnel-status").textContent,
}));
""".strip(),
    )

    after_start = cast(dict[str, JsonValue], payload["afterStart"])
    assert payload["startTunnelPayload"] == {"auto_save_webhook_base_url": True}
    assert payload["stopTunnelPayload"] == {"clear_webhook_base_url_if_matching": True}
    assert after_start["webhookBaseUrlValue"] == "https://demo-tunnel.lhr.life"
    assert (
        after_start["callbackPreviewText"]
        == "https://demo-tunnel.lhr.life/api/triggers/github/deliveries"
    )
    assert (
        after_start["tunnelStatusText"]
        == "Temporary public URL ready: https://demo-tunnel.lhr.life -> 127.0.0.1:8000"
    )
    assert payload["afterStopWebhookBaseUrlValue"] == ""
    assert (
        payload["afterStopCallbackPreviewText"]
        == "Configure a public base URL to generate the webhook callback address."
    )
    assert (
        payload["afterStopTunnelStatusText"]
        == "Temporary public URL stopped. Last address: https://demo-tunnel.lhr.life"
    )
    assert cast(list[dict[str, JsonValue]], payload["notifications"]) == [
        {
            "title": "Temporary Public URL Ready",
            "message": "Temporary public URL ready: https://demo-tunnel.lhr.life -> 127.0.0.1:8000",
            "tone": "success",
        },
        {
            "title": "Temporary Public URL Stopped",
            "message": "Temporary public URL stopped. Last address: https://demo-tunnel.lhr.life",
            "tone": "success",
        },
    ]


def test_github_settings_panel_explains_inactive_temporary_public_url(
    tmp_path: Path,
) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "token": None,
            "token_configured": False,
            "webhook_base_url": "https://expired-tunnel.lhr.life",
        },
        probe_webhook_result={
            "ok": False,
            "status_code": 503,
            "error_code": "temporary_public_url_inactive",
            "error_message": "Temporary public URL is inactive. Create a new temporary URL and retry.",
            "latency_ms": 31,
        },
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();
await document.getElementById("test-github-webhook-btn").onclick();

console.log(JSON.stringify({
    webhookProbeStatusText: document.getElementById("github-webhook-probe-status").textContent,
}));
""".strip(),
    )

    assert payload["webhookProbeStatusText"] == (
        "Webhook reachability failed for "
        "https://expired-tunnel.lhr.life/api/system/health: "
        "Temporary public URL is inactive. Create a new temporary URL and retry the callback test."
    )


def test_github_settings_panel_shows_empty_callback_preview_without_public_base_url(
    tmp_path: Path,
) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();

console.log(JSON.stringify({
    webhookBaseUrlValue: document.getElementById("github-webhook-base-url").value,
    callbackPreviewText: document.getElementById("github-callback-preview").textContent,
}));
""".strip(),
    )

    assert payload["webhookBaseUrlValue"] == ""
    assert (
        payload["callbackPreviewText"]
        == "Configure a public base URL to generate the webhook callback address."
    )


def test_github_settings_panel_requires_webhook_base_url_for_probe(
    tmp_path: Path,
) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();
await document.getElementById("test-github-webhook-btn").onclick();

console.log(JSON.stringify({
    webhookProbePayload: globalThis.__probeGitHubWebhookPayload,
    webhookProbeStatusText: document.getElementById("github-webhook-probe-status").textContent,
    webhookProbeStatusDisplay: document.getElementById("github-webhook-probe-status").style.display,
    webhookProbeButtonText: document.getElementById("test-github-webhook-btn").textContent,
}));
""".strip(),
    )

    assert payload["webhookProbePayload"] is None
    assert (
        payload["webhookProbeStatusText"]
        == "Configure a public Webhook Base URL before testing webhook reachability."
    )
    assert payload["webhookProbeStatusDisplay"] == "block"
    assert payload["webhookProbeButtonText"] == "Test Callback URL"


def test_github_settings_markup_includes_token_link_card() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    assert 'data-tab="github"' not in source

    github_source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "githubSettings.js"
    ).read_text(encoding="utf-8")

    assert 'id="github-token-link"' in github_source
    assert 'href="https://github.com/settings/tokens"' in github_source
    assert 'target="_blank"' in github_source
    assert 'rel="noreferrer"' in github_source
    assert 'id="${escapeHtml(ids.webhookBaseUrlInputId)}"' in github_source
    assert 'id="${escapeHtml(ids.callbackPreviewId)}"' in github_source
    assert 'id="${escapeHtml(ids.tunnelStartButtonId)}"' in github_source
    assert 'id="${escapeHtml(ids.tunnelStopButtonId)}"' in github_source
    assert 'id="${escapeHtml(ids.tunnelStatusId)}"' in github_source
    assert 'id="${escapeHtml(ids.webhookProbeButtonId)}"' in github_source
    assert 'id="${escapeHtml(ids.webhookSaveButtonId)}"' in github_source
    assert 'id="${escapeHtml(ids.webhookStatusId)}"' in github_source
    assert 'id="${escapeHtml(ids.clearTokenCheckboxId)}"' not in github_source
    assert 'id="${escapeHtml(ids.clearTokenRowId)}"' not in github_source
    assert "proxy-inline-field-actions" in github_source
    assert "settings-inline-action-row" in github_source
    assert "proxy-form-section-test" not in github_source
    assert github_source.count('<section class="proxy-form-section">') == 2


def _run_github_settings_script(
    tmp_path: Path,
    runner_source: str,
    *,
    fetch_config: dict[str, JsonValue] | None = None,
    probe_webhook_result: dict[str, JsonValue] | None = None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "githubSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "githubSettings.mjs"
    runner_path = tmp_path / "runner.mjs"
    fetch_github_config = fetch_config or {
        "token": None,
        "token_configured": False,
        "webhook_base_url": None,
    }
    fetch_github_config_json = json.dumps(fetch_github_config)
    probe_github_webhook_result = probe_webhook_result or {
        "ok": True,
        "status_code": 200,
        "latency_ms": 44,
        "error_code": None,
        "error_message": None,
    }
    probe_github_webhook_result_json = json.dumps(probe_github_webhook_result)
    mock_api_path.write_text(
        """
let currentConfig = __FETCH_GITHUB_CONFIG__;
let currentTunnelStatus = {
    status: "idle",
    public_url: null,
    local_host: null,
    local_port: null,
};

export async function fetchGitHubConfig() {
    return {
        token_configured: currentConfig.token_configured === true,
        webhook_base_url: currentConfig.webhook_base_url ?? null,
    };
}

export async function revealGitHubToken() {
    globalThis.__revealGitHubTokenCalls += 1;
    return {
        token: currentConfig.token || null,
    };
}

export async function fetchGitHubWebhookTunnelStatus() {
    return currentTunnelStatus;
}

export async function saveGitHubConfig(payload) {
    globalThis.__saveGitHubPayload = payload;
    globalThis.__saveGitHubPayloads.push(payload);
    currentConfig = {
        token: Object.prototype.hasOwnProperty.call(payload, "token")
            ? payload.token
            : currentConfig.token,
        token_configured: Boolean(payload.token) || currentConfig.token_configured === true,
        webhook_base_url: Object.prototype.hasOwnProperty.call(payload, "webhook_base_url")
            ? payload.webhook_base_url
            : currentConfig.webhook_base_url,
    };
    return { status: "ok" };
}

export async function probeGitHubConnectivity(payload) {
    globalThis.__probeGitHubPayload = payload;
    return {
        ok: true,
        username: "octocat",
        gh_version: "2.88.1",
        latency_ms: 42,
    };
}

export async function probeGitHubWebhookConnectivity(payload) {
    globalThis.__probeGitHubWebhookPayload = payload;
    return {
        webhook_base_url: payload.webhook_base_url,
        callback_url: `${payload.webhook_base_url}/api/triggers/github/deliveries`,
        health_url: `${payload.webhook_base_url}/api/system/health`,
        final_url: `${payload.webhook_base_url}/api/system/health`,
        ...__PROBE_GITHUB_WEBHOOK_RESULT__,
    };
}

export async function startGitHubWebhookTunnel(payload) {
    globalThis.__startTunnelPayload = payload;
    currentTunnelStatus = {
        status: "active",
        public_url: "https://demo-tunnel.lhr.life",
        local_host: "127.0.0.1",
        local_port: 8000,
    };
    if (payload.auto_save_webhook_base_url === true) {
        currentConfig = {
            ...currentConfig,
            webhook_base_url: currentTunnelStatus.public_url,
        };
    }
    return currentTunnelStatus;
}

export async function stopGitHubWebhookTunnel(payload) {
    globalThis.__stopTunnelPayload = payload;
    currentTunnelStatus = {
        ...currentTunnelStatus,
        status: "stopped",
    };
    if (
        payload.clear_webhook_base_url_if_matching === true
        && currentConfig.webhook_base_url === currentTunnelStatus.public_url
    ) {
        currentConfig = {
            ...currentConfig,
            webhook_base_url: null,
        };
    }
    return currentTunnelStatus;
}
""".replace("__FETCH_GITHUB_CONFIG__", fetch_github_config_json)
        .replace("__PROBE_GITHUB_WEBHOOK_RESULT__", probe_github_webhook_result_json)
        .strip(),
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
    "settings.github.load_failed": "Load Failed",
    "settings.github.load_failed_detail": "Failed to load GitHub settings: {error}",
    "settings.github.saved": "GitHub Settings Saved",
    "settings.github.saved_message": "GitHub settings saved.",
    "settings.github.save_failed": "Save Failed",
    "settings.github.save_failed_detail": "Failed to save GitHub settings: {error}",
    "settings.github.enter_token": "Enter a GitHub token before testing the connection.",
    "settings.github.testing_message": "Testing GitHub CLI connectivity...",
    "settings.github.probe_failed": "GitHub probe failed: {error}",
    "settings.github.probe_success": "{username} via {version} in {latency_ms}ms",
    "settings.github.probe_reason": "gh {version}: {reason}",
    "settings.github.webhook_probe_failed": "Webhook reachability probe failed: {error}",
    "settings.github.webhook_probe_success": "Webhook reachability OK: {status_code} in {latency_ms}ms via {final_url}",
    "settings.github.webhook_probe_reason": "Webhook reachability failed for {final_url}: {reason}",
    "settings.github.webhook_probe_temporary_public_url_inactive": "Temporary public URL is inactive. Create a new temporary URL and retry the callback test.",
    "settings.github.test_connection": "Test Connection",
    "settings.github.test_webhook": "Test Callback URL",
    "settings.github.testing": "Testing...",
    "settings.github.show_token": "Show GitHub token",
    "settings.github.hide_token": "Hide GitHub token",
    "settings.github.token_placeholder": "ghp_...",
    "settings.github.callback_preview_empty": "Configure a public base URL to generate the webhook callback address.",
    "settings.github.webhook_base_url_required": "Configure a public Webhook Base URL before testing webhook reachability.",
    "settings.github.webhook_testing_message": "Testing GitHub webhook reachability...",
    "settings.github.webhook_saved": "GitHub Webhook Saved",
    "settings.github.webhook_saved_message": "GitHub webhook settings saved.",
    "settings.github.webhook_save_failed": "Webhook Save Failed",
    "settings.github.tunnel_start": "Create Temporary URL",
    "settings.github.tunnel_stop": "Stop Temporary URL",
    "settings.github.tunnel_help": "Powered by localhost.run.",
    "settings.github.tunnel_idle": "No temporary public URL is running. Click \\"Create Temporary URL\\" to ask localhost.run for a random hostname.",
    "settings.github.tunnel_starting": "Requesting a temporary public URL from localhost.run...",
    "settings.github.tunnel_active": "Temporary public URL ready: {public_url} -> {local_host}:{local_port}",
    "settings.github.tunnel_stopped_message": "Temporary public URL stopped. Last address: {public_url}",
    "settings.github.tunnel_started": "Temporary Public URL Ready",
    "settings.github.tunnel_stopped": "Temporary Public URL Stopped",
    "settings.github.tunnel_start_failed": "Temporary Public URL Failed",
    "settings.github.tunnel_stop_failed": "Failed to Stop Temporary URL",
    "settings.github.tunnel_failed": "Temporary public URL failed: {reason}",
    "settings.github.tunnel_failed_unknown": "Unknown tunnel error",
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
        checked: false,
        disabled: false,
        textContent: "",
        innerHTML: "",
        className: "",
        onclick: null,
        onchange: null,
    }};
}}

function createElements() {{
    return new Map([
        ["github-token", createElement("block")],
        ["github-webhook-base-url", createElement("block")],
        ["github-callback-preview", createElement("block")],
        ["start-github-webhook-tunnel-btn", createElement("block")],
        ["stop-github-webhook-tunnel-btn", createElement("block")],
        ["github-webhook-tunnel-status", createElement("none")],
        ["toggle-github-token-btn", createElement("none")],
        ["github-probe-status", createElement("none")],
        ["github-webhook-probe-status", createElement("none")],
        ["save-github-btn", createElement("block")],
        ["test-github-btn", createElement("block")],
        ["save-github-webhook-btn", createElement("block")],
        ["test-github-webhook-btn", createElement("block")],
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
        addEventListener() {{
            return undefined;
        }},
    }};
    globalThis.__feedbackNotifications = notifications;
    globalThis.__saveGitHubPayload = null;
    globalThis.__saveGitHubPayloads = [];
    globalThis.__probeGitHubPayload = null;
    globalThis.__probeGitHubWebhookPayload = null;
    globalThis.__revealGitHubTokenCalls = 0;
    globalThis.__startTunnelPayload = null;
    globalThis.__stopTunnelPayload = null;
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
