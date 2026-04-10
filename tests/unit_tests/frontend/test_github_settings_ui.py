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

await document.getElementById("test-github-btn").onclick();
await document.getElementById("save-github-btn").onclick();

console.log(JSON.stringify({
    notifications,
    tokenValue: document.getElementById("github-token").value,
    tokenPlaceholder: document.getElementById("github-token").placeholder,
    tokenType: document.getElementById("github-token").type,
    toggleDisplay: document.getElementById("toggle-github-token-btn").style.display,
    savePayload: globalThis.__saveGitHubPayload,
    probePayload: globalThis.__probeGitHubPayload,
    probeStatusText: document.getElementById("github-probe-status").textContent,
    probeStatusDisplay: document.getElementById("github-probe-status").style.display,
    probeButtonText: document.getElementById("test-github-btn").textContent,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "************"
    assert payload["tokenType"] == "password"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["savePayload"] == {"token": "ghp_secret"}
    assert payload["probePayload"] == {"token": "ghp_secret"}
    assert payload["probeStatusDisplay"] == "block"
    assert "octocat via gh 2.88.1 in 42ms" in cast(str, payload["probeStatusText"])
    assert payload["probeButtonText"] == "Test Connection"
    assert notifications == [
        {
            "title": "GitHub Settings Saved",
            "message": "GitHub settings saved.",
            "tone": "success",
        }
    ]


def test_github_settings_panel_preserves_saved_token_for_probe_and_save(
    tmp_path: Path,
) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        fetch_config={"token": "ghp_saved"},
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();

document.getElementById("toggle-github-token-btn").onclick();
const revealedValue = document.getElementById("github-token").value;
const revealedType = document.getElementById("github-token").type;
const toggleTitle = document.getElementById("toggle-github-token-btn").title;
document.getElementById("toggle-github-token-btn").onclick();

await document.getElementById("test-github-btn").onclick();
await document.getElementById("save-github-btn").onclick();

console.log(JSON.stringify({
    revealedValue,
    revealedType,
    toggleTitle,
    tokenValue: document.getElementById("github-token").value,
    tokenPlaceholder: document.getElementById("github-token").placeholder,
    toggleDisplay: document.getElementById("toggle-github-token-btn").style.display,
    savePayload: globalThis.__saveGitHubPayload,
    probePayload: globalThis.__probeGitHubPayload,
}));
""".strip(),
    )

    assert payload["revealedValue"] == "ghp_saved"
    assert payload["revealedType"] == "text"
    assert payload["toggleTitle"] == "Hide GitHub token"
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "************"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["savePayload"] == {"token": "ghp_saved"}
    assert payload["probePayload"] == {"token": "ghp_saved"}


def test_github_settings_panel_allows_clearing_saved_token(tmp_path: Path) -> None:
    payload = _run_github_settings_script(
        tmp_path=tmp_path,
        fetch_config={"token": "ghp_saved"},
        runner_source="""
import { bindGitHubSettingsHandlers, loadGitHubSettingsPanel } from "./githubSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindGitHubSettingsHandlers();
await loadGitHubSettingsPanel();

document.getElementById("toggle-github-token-btn").onclick();
document.getElementById("github-token").value = "";
document.getElementById("github-token").oninput();

await document.getElementById("test-github-btn").onclick();
await document.getElementById("save-github-btn").onclick();

console.log(JSON.stringify({
    notifications,
    tokenValue: document.getElementById("github-token").value,
    tokenPlaceholder: document.getElementById("github-token").placeholder,
    toggleDisplay: document.getElementById("toggle-github-token-btn").style.display,
    savePayload: globalThis.__saveGitHubPayload,
    probePayload: globalThis.__probeGitHubPayload,
    probeStatusText: document.getElementById("github-probe-status").textContent,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["tokenValue"] == ""
    assert payload["tokenPlaceholder"] == "ghp_..."
    assert payload["toggleDisplay"] == "none"
    assert payload["savePayload"] == {"token": None}
    assert payload["probePayload"] is None
    assert (
        payload["probeStatusText"]
        == "Enter a GitHub token before testing the connection."
    )
    assert notifications == [
        {
            "title": "GitHub Settings Saved",
            "message": "GitHub settings saved.",
            "tone": "success",
        }
    ]


def test_github_settings_markup_includes_token_link_card() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    assert 'id="github-token-link"' in source
    assert 'href="https://github.com/settings/tokens"' in source
    assert 'target="_blank"' in source
    assert 'rel="noreferrer"' in source


def _run_github_settings_script(
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
    }
    fetch_github_config_json = json.dumps(fetch_github_config)

    mock_api_path.write_text(
        """
let currentConfig = __FETCH_GITHUB_CONFIG__;

export async function fetchGitHubConfig() {
    return currentConfig;
}

export async function saveGitHubConfig(payload) {
    globalThis.__saveGitHubPayload = payload;
    currentConfig = payload;
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
""".replace("__FETCH_GITHUB_CONFIG__", fetch_github_config_json).strip(),
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
    "settings.github.test_connection": "Test Connection",
    "settings.github.testing": "Testing...",
    "settings.github.show_token": "Show GitHub token",
    "settings.github.hide_token": "Hide GitHub token",
    "settings.github.token_placeholder": "ghp_...",
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
        ["github-token", createElement("block")],
        ["toggle-github-token-btn", createElement("none")],
        ["github-probe-status", createElement("none")],
        ["save-github-btn", createElement("block")],
        ["test-github-btn", createElement("block")],
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
    globalThis.__probeGitHubPayload = null;
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
