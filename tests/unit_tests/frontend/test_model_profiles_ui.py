# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from agent_teams.shared_types.json_types import JsonObject


def test_saving_model_profile_restores_profile_list_visibility(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "ui-regression-profile";
document.getElementById("profile-model").value = "fake-chat-model";
document.getElementById("profile-base-url").value = "http://127.0.0.1:8001/v1";
document.getElementById("profile-api-key").value = "test-api-key";
document.getElementById("profile-temperature").value = "0.3";
document.getElementById("profile-top-p").value = "0.8";
document.getElementById("profile-max-tokens").value = "512";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    listDisplay: document.getElementById("profiles-list").style.display,
    editorDisplay: document.getElementById("profile-editor").style.display,
    addButtonDisplay: document.getElementById("add-profile-btn").style.display,
    renderedHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
    )

    rendered_html = cast(str, payload["renderedHtml"])
    notifications = cast(list[JsonObject], payload["notifications"])
    assert notifications == [
        {
            "title": "Profile Saved",
            "message": "Profile saved and reloaded.",
            "tone": "success",
        }
    ]
    assert payload["listDisplay"] == "block"
    assert payload["editorDisplay"] == "none"
    assert payload["addButtonDisplay"] == "inline-flex"
    assert "ui-regression-profile" in rendered_html


def test_draft_probe_updates_inline_status_and_payload(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-model").value = "draft-model";
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";
document.getElementById("profile-temperature").value = "0.4";
document.getElementById("profile-top-p").value = "0.9";
document.getElementById("profile-max-tokens").value = "256";

await document.getElementById("test-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    testButtonText: document.getElementById("test-profile-btn").textContent,
    probeStatusText: document.getElementById("profile-probe-status").textContent,
    probeStatusDisplay: document.getElementById("profile-probe-status").style.display,
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    probe_payload = cast(JsonObject, payload["probePayload"])
    probe_override = cast(JsonObject, probe_payload["override"])
    probe_status_text = cast(str, payload["probeStatusText"])
    assert payload["notifications"] == []
    assert payload["testButtonText"] == "Test"
    assert payload["probeStatusDisplay"] == "block"
    assert "Connected in 42ms" in probe_status_text
    assert "9 tokens" in probe_status_text
    assert probe_payload["timeout_ms"] == 15000
    assert probe_override["model"] == "draft-model"
    assert probe_override["base_url"] == "https://draft.test/v1"
    assert probe_override["api_key"] == "draft-api-key"


def test_edit_profile_preserves_existing_api_key_when_left_blank(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-top-p").value = "0.95";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    apiKeyPlaceholder: document.getElementById("profile-api-key").placeholder,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(JsonObject, payload["savedProfile"])
    saved_profile_body = cast(JsonObject, saved_profile["profile"])
    notifications = cast(list[JsonObject], payload["notifications"])
    assert notifications == [
        {
            "title": "Profile Saved",
            "message": "Profile saved and reloaded.",
            "tone": "success",
        }
    ]
    assert payload["apiKeyPlaceholder"] == "Leave blank to keep current API key"
    assert saved_profile["name"] == "default"
    assert "api_key" not in saved_profile_body
    assert saved_profile_body["top_p"] == 0.95


def test_edit_profile_allows_renaming_and_sends_source_name(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-name").value = "renamed-profile";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    nameDisabled: document.getElementById("profile-name").disabled,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(JsonObject, payload["savedProfile"])
    saved_profile_body = cast(JsonObject, saved_profile["profile"])
    assert payload["nameDisabled"] is False
    assert saved_profile["name"] == "renamed-profile"
    assert saved_profile_body["source_name"] == "default"


def test_saved_profile_probe_uses_profile_connect_timeout(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
await loadModelProfilesPanel();

await document.getElementById("profiles-list").querySelectorAll(".profile-card-test-btn")[0].onclick();

console.log(JSON.stringify({
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    probe_payload = cast(JsonObject, payload["probePayload"])
    assert probe_payload["profile_name"] == "default"
    assert probe_payload["timeout_ms"] == 15000


def test_model_profile_cards_render_inline_probe_region(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
await loadModelProfilesPanel();

console.log(JSON.stringify({
    renderedHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
    )

    rendered_html = cast(str, payload["renderedHtml"])
    assert "profile-records" in rendered_html
    assert "profile-card-inline-status" in rendered_html
    assert "profile-cards" not in rendered_html
    assert "API Key" not in rendered_html
    assert "Temperature" not in rendered_html
    assert "Top P" not in rendered_html
    assert "Max Output Tokens" not in rendered_html
    assert "Connect Timeout" not in rendered_html


def test_deleting_profile_uses_custom_confirm_and_success_notification(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

await document.getElementById("profiles-list").querySelectorAll(".delete-profile-btn")[0].onclick();

console.log(JSON.stringify({
    notifications,
    confirms: globalThis.__feedbackConfirms,
    deletedProfileName: globalThis.__deletedProfileName,
}));
""".strip(),
    )

    notifications = cast(list[JsonObject], payload["notifications"])
    confirms = cast(list[JsonObject], payload["confirms"])
    assert payload["deletedProfileName"] == "default"
    assert confirms == [
        {
            "title": "Delete Profile",
            "message": 'Delete profile "default"?',
            "tone": "warning",
            "confirmLabel": "Delete",
            "cancelLabel": "Cancel",
        }
    ]
    assert notifications == [
        {
            "title": "Profile Deleted",
            "message": "Profile deleted and reloaded.",
            "tone": "success",
        }
    ]


def _run_model_profiles_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "modelProfiles.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    module_under_test_path = tmp_path / "modelProfiles.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchModelProfiles() {
    return {
        default: {
            provider: "openai_compatible",
            model: "fake-chat-model",
            base_url: "http://127.0.0.1:8001/v1",
            has_api_key: true,
            temperature: 0.3,
            top_p: 0.8,
            max_tokens: 512,
            connect_timeout_seconds: 15,
        },
        "ui-regression-profile": {
            provider: "openai_compatible",
            model: "fake-chat-model",
            base_url: "http://127.0.0.1:8001/v1",
            has_api_key: true,
            temperature: 0.3,
            top_p: 0.8,
            max_tokens: 512,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return {
        ok: true,
        latency_ms: 42,
        token_usage: {
            total_tokens: 9,
        },
    };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
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
    mock_feedback_path.write_text(
        """
export function showToast(payload) {
    globalThis.__feedbackNotifications.push(payload);
}

export async function showConfirmDialog(payload) {
    globalThis.__feedbackConfirms.push(payload);
    return true;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createElement(initialDisplay = "block") {{
    let lastQuerySource = "";
    const queryCache = new Map();

    function collectMatches(source, selector) {{
        const selectorToClass = new Map([
            [".edit-profile-btn", "edit-profile-btn"],
            [".delete-profile-btn", "delete-profile-btn"],
            [".profile-card-test-btn", "profile-card-test-btn"],
        ]);
        const className = selectorToClass.get(selector);
        if (!className) {{
            return [];
        }}
        const pattern = new RegExp(`class="[^"]*${{className}}[^"]*"[^>]*data-name="([^"]+)"`, "g");
        const matches = [];
        let match = pattern.exec(source);
        while (match) {{
            matches.push({{
                dataset: {{ name: match[1] }},
                onclick: null,
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    return {{
        style: {{ display: initialDisplay }},
        value: "",
        disabled: false,
        placeholder: "",
        textContent: "",
        innerHTML: "",
        className: "",
        dataset: {{}},
        onclick: null,
        focused: false,
        focus() {{
            this.focused = true;
        }},
        querySelectorAll(selector) {{
            if (this.innerHTML !== lastQuerySource) {{
                queryCache.clear();
                lastQuerySource = this.innerHTML;
            }}
            if (!queryCache.has(selector)) {{
                queryCache.set(selector, collectMatches(this.innerHTML, selector));
            }}
            return queryCache.get(selector) || [];
        }},
    }};
}}

function createElements() {{
    return new Map([
        ["profiles-list", createElement("block")],
        ["profile-editor", createElement("none")],
        ["add-profile-btn", createElement("block")],
        ["save-profile-btn", createElement("block")],
        ["test-profile-btn", createElement("block")],
        ["cancel-profile-btn", createElement("block")],
        ["profile-probe-status", createElement("none")],
        ["profile-editor-title", createElement("block")],
        ["profile-name", createElement("block")],
        ["profile-model", createElement("block")],
        ["profile-base-url", createElement("block")],
        ["profile-api-key", createElement("block")],
        ["profile-temperature", createElement("block")],
        ["profile-top-p", createElement("block")],
        ["profile-max-tokens", createElement("block")],
        ["profile-connect-timeout", createElement("block")],
    ]);
}}

function installGlobals(elements, notifications) {{
    function collectDocumentMatches(selector) {{
        if (selector !== ".profile-card") {{
            return [];
        }}
        const source = elements.get("profiles-list")?.innerHTML || "";
        const pattern = /data-profile-name="([^"]+)"/g;
        const matches = [];
        let match = pattern.exec(source);
        while (match) {{
            const profileName = match[1];
            matches.push({{
                dataset: {{ profileName }},
                querySelector(innerSelector) {{
                    if (innerSelector === ".profile-card-test-btn") {{
                        return elements
                            .get("profiles-list")
                            ?.querySelectorAll(".profile-card-test-btn")
                            .find(candidate => candidate.dataset.name === profileName) || null;
                    }}
                    if (innerSelector === "[data-profile-probe-container]") {{
                        return {{
                            innerHTML: "",
                        }};
                    }}
                    return null;
                }},
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
        querySelectorAll(selector) {{
            return collectDocumentMatches(selector);
        }},
    }};
    globalThis.__feedbackNotifications = notifications;
    globalThis.__feedbackConfirms = [];
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
