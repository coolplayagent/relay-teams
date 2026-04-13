# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast


def test_clawhub_settings_panel_loads_saves_and_probes(tmp_path: Path) -> None:
    payload = _run_clawhub_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindClawHubSettingsHandlers, loadClawHubSettingsPanel } from "./clawhubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindClawHubSettingsHandlers();
await loadClawHubSettingsPanel();

document.getElementById("clawhub-token").value = "ch_secret";
document.getElementById("clawhub-token").oninput();

await document.getElementById("test-clawhub-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));
await document.getElementById("save-clawhub-token-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    notifications,
    tokenValue: document.getElementById("clawhub-token").value,
    tokenPlaceholder: document.getElementById("clawhub-token").placeholder,
    tokenType: document.getElementById("clawhub-token").type,
    toggleDisplay: document.getElementById("toggle-clawhub-token-btn").style.display,
    savePayload: globalThis.__saveClawHubPayload,
    probePayload: globalThis.__probeClawHubPayload,
    probeStatusText: document.getElementById("clawhub-probe-status").textContent,
    probeStatusDisplay: document.getElementById("clawhub-probe-status").style.display,
    probeButtonText: document.getElementById("test-clawhub-btn").textContent,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "************"
    assert payload["tokenType"] == "password"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["savePayload"] == {"token": "ch_secret"}
    assert payload["probePayload"] == {"token": "ch_secret"}
    assert payload["probeStatusDisplay"] == "block"
    assert "Authenticated with the configured token via clawhub 0.4.2 in 37ms" in cast(
        str, payload["probeStatusText"]
    )
    assert payload["probeButtonText"] == "Test Connection"
    assert notifications == [
        {
            "title": "ClawHub Settings Saved",
            "message": "ClawHub settings saved.",
            "tone": "success",
        }
    ]


def test_clawhub_settings_panel_preserves_saved_token_for_probe_and_save(
    tmp_path: Path,
) -> None:
    payload = _run_clawhub_settings_script(
        tmp_path=tmp_path,
        fetch_config={"token": "ch_saved"},
        runner_source="""
import { bindClawHubSettingsHandlers, loadClawHubSettingsPanel } from "./clawhubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindClawHubSettingsHandlers();
await loadClawHubSettingsPanel();

document.getElementById("toggle-clawhub-token-btn").onclick();
const revealedValue = document.getElementById("clawhub-token").value;
const revealedType = document.getElementById("clawhub-token").type;
const toggleTitle = document.getElementById("toggle-clawhub-token-btn").title;
document.getElementById("toggle-clawhub-token-btn").onclick();

await document.getElementById("test-clawhub-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));
await document.getElementById("save-clawhub-token-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    revealedValue,
    revealedType,
    toggleTitle,
    tokenValue: document.getElementById("clawhub-token").value,
    tokenPlaceholder: document.getElementById("clawhub-token").placeholder,
    toggleDisplay: document.getElementById("toggle-clawhub-token-btn").style.display,
    savePayload: globalThis.__saveClawHubPayload,
    probePayload: globalThis.__probeClawHubPayload,
}));
""".strip(),
    )

    assert payload["revealedValue"] == "ch_saved"
    assert payload["revealedType"] == "text"
    assert payload["toggleTitle"] == "Hide ClawHub token"
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "************"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["savePayload"] == {"token": "ch_saved"}
    assert payload["probePayload"] == {"token": "ch_saved"}


def test_clawhub_settings_panel_shows_auto_install_success_message(
    tmp_path: Path,
) -> None:
    payload = _run_clawhub_settings_script(
        tmp_path=tmp_path,
        probe_response={
            "ok": True,
            "clawhub_version": "clawhub 0.9.0",
            "latency_ms": 4200,
            "diagnostics": {
                "installed_during_probe": True,
            },
        },
        runner_source="""
import { bindClawHubSettingsHandlers, loadClawHubSettingsPanel } from "./clawhubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindClawHubSettingsHandlers();
await loadClawHubSettingsPanel();

document.getElementById("clawhub-token").value = "ch_secret";
document.getElementById("clawhub-token").oninput();

await document.getElementById("test-clawhub-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    probeStatusText: document.getElementById("clawhub-probe-status").textContent,
}));
""".strip(),
    )

    assert (
        payload["probeStatusText"]
        == "Authenticated with the configured token via clawhub 0.9.0 in 4200ms. Installed automatically."
    )


def test_clawhub_settings_panel_allows_clearing_saved_token(tmp_path: Path) -> None:
    payload = _run_clawhub_settings_script(
        tmp_path=tmp_path,
        fetch_config={"token": "ch_saved"},
        runner_source="""
import { bindClawHubSettingsHandlers, loadClawHubSettingsPanel } from "./clawhubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindClawHubSettingsHandlers();
await loadClawHubSettingsPanel();

document.getElementById("toggle-clawhub-token-btn").onclick();
document.getElementById("clawhub-token").value = "";
document.getElementById("clawhub-token").oninput();

await document.getElementById("test-clawhub-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));
await document.getElementById("save-clawhub-token-btn").onclick();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    notifications,
    tokenValue: document.getElementById("clawhub-token").value,
    tokenPlaceholder: document.getElementById("clawhub-token").placeholder,
    toggleDisplay: document.getElementById("toggle-clawhub-token-btn").style.display,
    savePayload: globalThis.__saveClawHubPayload,
    probePayload: globalThis.__probeClawHubPayload,
    probeStatusText: document.getElementById("clawhub-probe-status").textContent,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "ch_..."
    assert payload["toggleDisplay"] == "none"
    assert payload["savePayload"] == {"token": None}
    assert payload["probePayload"] is None
    assert (
        payload["probeStatusText"]
        == "Enter a ClawHub token before testing the connection."
    )
    assert notifications == [
        {
            "title": "ClawHub Settings Saved",
            "message": "ClawHub settings saved.",
            "tone": "success",
        }
    ]


def test_clawhub_settings_markup_lives_in_skills_feature_and_keeps_actions_inline() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[3]
    project_view_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")
    skills_feature_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "projectView.js"
    )
    source = skills_feature_source.read_text(encoding="utf-8")

    assert 'id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.probeButtonId)}"' in source
    assert 'id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.saveButtonId)}"' in source
    assert 'class="settings-inline-action-row"' in source
    assert 'id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.statusId)}"' in source
    assert 'id="feature-clawhub-token-link"' in source
    assert 'href="https://clawhub.ai/settings"' in source
    assert 'target="_blank"' in source
    assert 'rel="noreferrer"' in source
    assert 'id="clawhub-token"' not in project_view_source


def _run_clawhub_settings_script(
    tmp_path: Path,
    runner_source: str,
    *,
    fetch_config: dict[str, JsonValue] | None = None,
    probe_response: dict[str, JsonValue] | None = None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "clawhubSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "clawhubSettings.mjs"
    runner_path = tmp_path / "runner.mjs"
    fetch_clawhub_config = fetch_config or {"token": None}
    fetch_clawhub_config_json = json.dumps(fetch_clawhub_config)
    resolved_probe_response = probe_response or {
        "ok": True,
        "clawhub_version": "clawhub 0.4.2",
        "latency_ms": 37,
    }
    probe_response_json = json.dumps(resolved_probe_response)

    mock_api_path.write_text(
        """
let currentConfig = __FETCH_CLAWHUB_CONFIG__;

export async function fetchClawHubConfig() {
    return currentConfig;
}

export async function saveClawHubConfig(payload) {
    globalThis.__saveClawHubPayload = payload;
    currentConfig = payload;
    return { status: "ok" };
}

export async function probeClawHubConnectivity(payload) {
    globalThis.__probeClawHubPayload = payload;
    return __PROBE_RESPONSE__;
}
""".replace("__FETCH_CLAWHUB_CONFIG__", fetch_clawhub_config_json)
        .replace("__PROBE_RESPONSE__", probe_response_json)
        .strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export function showToast(payload) {
    globalThis.__notifications.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.clawhub.token_placeholder": "ch_...",
    "settings.clawhub.saved": "ClawHub Settings Saved",
    "settings.clawhub.saved_message": "ClawHub settings saved.",
    "settings.clawhub.save_failed": "Save Failed",
    "settings.clawhub.load_failed": "Load Failed",
    "settings.clawhub.enter_token": "Enter a ClawHub token before testing the connection.",
    "settings.clawhub.testing_message": "Authenticating to ClawHub with the configured token...",
    "settings.clawhub.probe_failed": "ClawHub probe failed: {error}",
    "settings.clawhub.probe_success": "Authenticated with the configured token via {version} in {latency_ms}ms",
    "settings.clawhub.probe_success_after_install": "Authenticated with the configured token via {version} in {latency_ms}ms. Installed automatically.",
    "settings.clawhub.probe_reason": "{reason}",
    "settings.clawhub.test_connection": "Test Connection",
    "settings.clawhub.testing": "Testing...",
    "settings.clawhub.show_token": "Show ClawHub token",
    "settings.clawhub.hide_token": "Hide ClawHub token",
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
export function errorToPayload(error) {
    return { error: String(error) };
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
        title: "",
        type: "password",
        placeholder: "",
        onclick: null,
        oninput: null,
        onchange: null,
        setAttribute(name, value) {{
            this[name] = value;
        }},
    }};
}}

function createElements() {{
    return new Map([
        ["clawhub-token", createElement("block")],
        ["toggle-clawhub-token-btn", createElement("none")],
        ["clawhub-probe-status", createElement("none")],
        ["save-clawhub-token-btn", createElement("block")],
        ["test-clawhub-btn", createElement("block")],
    ]);
}}

function installGlobals(elements, notifications) {{
    globalThis.__saveClawHubPayload = null;
    globalThis.__probeClawHubPayload = null;
    globalThis.__notifications = notifications;
    globalThis.document = {{
        getElementById(id) {{
            return elements.get(id) || null;
        }},
        addEventListener() {{
            return undefined;
        }},
    }};
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
        encoding="utf-8",
    )

    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return cast(dict[str, object], json.loads(completed.stdout))
