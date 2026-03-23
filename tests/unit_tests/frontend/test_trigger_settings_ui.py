# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from pydantic import JsonValue


def test_trigger_settings_renders_feishu_platform_and_opens_detail(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();

const platformHtml = document.getElementById("trigger-platform-list").innerHTML;
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    notifications,
    platformHtml,
    detailHtml: document.getElementById("trigger-provider-detail").innerHTML,
    platformDisplay: document.getElementById("trigger-platform-list").style.display,
    detailDisplay: document.getElementById("trigger-provider-detail-panel").style.display,
    addDisplay: document.getElementById("add-trigger-btn").style.display,
    saveDisplay: document.getElementById("save-trigger-btn").style.display,
    actionsBarDisplay: document.getElementById("settings-actions-bar").style.display,
}));
""".strip(),
    )

    platform_html = cast(str, payload["platformHtml"])
    detail_html = cast(str, payload["detailHtml"])
    assert payload["notifications"] == []
    assert "Feishu" in platform_html
    assert "1 triggers" in platform_html
    assert "Credentials Missing" in platform_html
    assert "http_bridge" not in detail_html
    assert "feishu_main" in detail_html
    assert "Credentials" in detail_html
    assert payload["platformDisplay"] == "none"
    assert payload["detailDisplay"] == "block"
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"
    assert payload["actionsBarDisplay"] == "flex"


def test_trigger_settings_adds_feishu_trigger_and_saves_credentials(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
globalThis.__triggerFixtures = [];
globalThis.__environmentFixtures = {
    app: [],
    system: [],
};

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("add-trigger-btn").onclick();

document.getElementById("feishu-app-id-input").value = "cli_demo";
document.getElementById("feishu-app-id-input").oninput();
document.getElementById("feishu-app-secret-input").value = "secret-demo";
document.getElementById("feishu-app-secret-input").oninput();
document.getElementById("feishu-trigger-name-input").value = "feishu_ops";
document.getElementById("feishu-trigger-name-input").oninput();
document.getElementById("feishu-trigger-display-name-input").value = "Feishu Ops";
document.getElementById("feishu-trigger-display-name-input").oninput();
document.getElementById("feishu-trigger-workspace-id-input").value = "workspace-ops";
document.getElementById("feishu-trigger-workspace-id-input").oninput();
document.getElementById("feishu-trigger-rule-input").value = "mention_only";
document.getElementById("feishu-trigger-rule-input").onchange();
document.getElementById("feishu-trigger-enabled-input").checked = false;
document.getElementById("feishu-trigger-enabled-input").onchange();

await document.getElementById("save-trigger-btn").onclick();

console.log(JSON.stringify({
    notifications,
    envSaves: globalThis.__envSaveCalls,
    envDeletes: globalThis.__envDeleteCalls,
    createCalls: globalThis.__createTriggerCalls,
    updateCalls: globalThis.__updateTriggerCalls,
    enableCalls: globalThis.__enableTriggerCalls,
    disableCalls: globalThis.__disableTriggerCalls,
    addDisplay: document.getElementById("add-trigger-btn").style.display,
    saveDisplay: document.getElementById("save-trigger-btn").style.display,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    env_saves = cast(list[dict[str, JsonValue]], payload["envSaves"])
    create_calls = cast(list[dict[str, JsonValue]], payload["createCalls"])
    assert payload["envDeletes"] == []
    assert payload["updateCalls"] == []
    assert payload["enableCalls"] == []
    assert payload["disableCalls"] == []
    assert len(env_saves) == 2
    assert env_saves[0]["key"] == "FEISHU_APP_ID"
    assert env_saves[1]["key"] == "FEISHU_APP_SECRET"
    assert len(create_calls) == 1
    create_payload = cast(dict[str, JsonValue], create_calls[0]["payload"])
    assert create_payload["name"] == "feishu_ops"
    assert create_payload["display_name"] == "Feishu Ops"
    assert create_payload["source_type"] == "im"
    assert create_payload["enabled"] is False
    assert create_payload["source_config"] == {
        "provider": "feishu",
        "trigger_rule": "mention_only",
    }
    assert create_payload["target_config"] == {
        "workspace_id": "workspace-ops",
    }
    assert notifications == [
        {
            "title": "Trigger Settings Saved",
            "message": "Feishu trigger settings saved.",
            "tone": "success",
        }
    ]
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"

def _run_trigger_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "triggerSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "triggerSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchTriggers() {
    return globalThis.__triggerFixtures;
}

export async function fetchEnvironmentVariables() {
    return globalThis.__environmentFixtures;
}

export async function saveEnvironmentVariable(scope, key, payload) {
    globalThis.__envSaveCalls.push({ scope, key, payload });
    return { key, value: payload.value, scope, value_kind: "string" };
}

export async function deleteEnvironmentVariable(scope, key) {
    globalThis.__envDeleteCalls.push({ scope, key });
    return { status: "ok" };
}

export async function createTrigger(payload) {
    globalThis.__createTriggerCalls.push({ payload });
    return { trigger_id: "trigger-created", ...payload };
}

export async function updateTrigger(triggerId, payload) {
    globalThis.__updateTriggerCalls.push({ triggerId, payload });
    return { trigger_id: triggerId, ...payload };
}

export async function enableTrigger(triggerId) {
    globalThis.__enableTriggerCalls.push(triggerId);
    return { status: "enabled" };
}

export async function disableTrigger(triggerId) {
    globalThis.__disableTriggerCalls.push(triggerId);
    return { status: "disabled" };
}

export async function rotateTriggerToken(triggerId) {
    globalThis.__rotateTriggerCalls.push(triggerId);
    return { status: "ok" };
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
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.triggers.feishu": "Feishu",
    "settings.triggers.configure": "Configure",
    "settings.triggers.ready": "Ready",
    "settings.triggers.credentials_missing": "Credentials Missing",
    "settings.triggers.trigger_count": "{count} triggers",
    "settings.triggers.enabled_count": "{count} enabled",
    "settings.triggers.back": "Back",
    "settings.triggers.feishu_detail_copy": "Manage shared Feishu app credentials and trigger records.",
    "settings.triggers.credentials": "Credentials",
    "settings.triggers.credentials_copy": "Shared app credentials.",
    "settings.triggers.sdk_mode_note": "Feishu inbound messages use the SDK long connection mode.",
    "settings.triggers.encrypt_key_note": "Set FEISHU_ENCRYPT_KEY only if you enabled encrypted event delivery in Feishu.",
    "settings.triggers.records": "Triggers",
    "settings.triggers.records_copy": "Provider-specific trigger records.",
    "settings.triggers.none": "No Feishu triggers",
    "settings.triggers.none_copy": "Add a Feishu trigger.",
    "settings.triggers.editor": "Trigger Editor",
    "settings.triggers.editing_existing": "Editing existing trigger",
    "settings.triggers.editing_new": "New trigger",
    "settings.triggers.trigger_name": "Trigger Name",
    "settings.triggers.display_name": "Display Name",
    "settings.triggers.workspace": "Workspace ID",
    "settings.triggers.rule": "Trigger Rule",
    "settings.triggers.enable_trigger": "Enable trigger",
    "settings.triggers.provider": "Provider",
    "settings.triggers.source_type": "Source Type",
    "settings.triggers.credentials_ready": "Credentials ready",
    "settings.triggers.credentials_missing_count": "{count} credentials missing",
    "settings.triggers.saved": "Trigger Settings Saved",
    "settings.triggers.saved_message": "Feishu trigger settings saved.",
    "settings.triggers.save_failed": "Save Failed",
    "settings.triggers.load_failed": "Load Failed",
    "settings.triggers.missing_name": "Trigger name is required.",
    "settings.triggers.missing_workspace": "Workspace ID is required.",
    "settings.triggers.unnamed": "Unnamed trigger",
    "settings.triggers.feishu_app_id": "Feishu App ID",
    "settings.triggers.feishu_app_id_placeholder": "cli_xxx",
    "settings.triggers.feishu_app_secret": "Feishu App Secret",
    "settings.triggers.feishu_app_secret_placeholder": "App secret",
    "settings.field.enabled": "Enabled",
    "settings.roles.disabled": "Disabled",
    "settings.roles.edit": "Edit",
};

export function t(key) {
    return translations[key] || key;
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
    const element = {{
        style: {{ display: initialDisplay }},
        value: "",
        checked: true,
        textContent: "",
        onclick: null,
        oninput: null,
        onchange: null,
        dataset: {{}},
        __selectorCache: new Map(),
    }};

    element.classList = {{
        add() {{
            return undefined;
        }},
        remove() {{
            return undefined;
        }},
    }};

    Object.defineProperty(element, "innerHTML", {{
        get() {{
            return this.__html || "";
        }},
        set(value) {{
            this.__html = String(value || "");
            this.__selectorCache = new Map();
        }},
    }});

    element.querySelectorAll = selector => {{
        if (!element.__selectorCache.has(selector)) {{
            element.__selectorCache.set(selector, parseSelector(element.innerHTML, selector));
        }}
        return element.__selectorCache.get(selector);
    }};
    return element;
}}

function parseSelector(html, selector) {{
    const configs = {{
        ".trigger-platform-open-btn": /class="[^"]*trigger-platform-open-btn[^"]*"[^>]*data-trigger-platform="([^"]+)"/g,
        ".trigger-platform-record": /class="[^"]*trigger-platform-record[^"]*"[^>]*data-trigger-platform="([^"]+)"/g,
        ".trigger-record": /class="[^"]*trigger-record[^"]*"[^>]*data-trigger-id="([^"]+)"/g,
        ".trigger-record-edit-btn": /class="[^"]*trigger-record-edit-btn[^"]*"[^>]*data-trigger-id="([^"]+)"/g,
    }};
    const config = configs[selector];
    if (!config) {{
        return [];
    }}
    const matches = [];
    for (const match of html.matchAll(config)) {{
        const child = createElement();
        if (selector === ".trigger-platform-open-btn" || selector === ".trigger-platform-record") {{
            child.dataset.triggerPlatform = match[1];
        }} else {{
            child.dataset.triggerId = match[1];
        }}
        matches.push(child);
    }}
    return matches;
}}

function createElements() {{
    const elements = new Map([
        ["settings-actions-bar", createElement("flex")],
        ["add-trigger-btn", createElement("none")],
        ["save-trigger-btn", createElement("none")],
        ["cancel-trigger-btn", createElement("none")],
        ["trigger-platform-list", createElement("block")],
        ["trigger-provider-detail-panel", createElement("none")],
        ["trigger-provider-detail", createElement("block")],
        ["trigger-editor-status", createElement("none")],
        ["trigger-provider-back-btn", createElement("block")],
        ["feishu-app-id-input", createElement("block")],
        ["feishu-app-secret-input", createElement("block")],
        ["feishu-trigger-name-input", createElement("block")],
        ["feishu-trigger-display-name-input", createElement("block")],
        ["feishu-trigger-workspace-id-input", createElement("block")],
        ["feishu-trigger-rule-input", createElement("block")],
        ["feishu-trigger-enabled-input", createElement("block")],
    ]);
    return elements;
}}

function installGlobals(elements, notifications) {{
    globalThis.__feedbackNotifications = notifications;
    globalThis.__envSaveCalls = [];
    globalThis.__envDeleteCalls = [];
    globalThis.__createTriggerCalls = [];
    globalThis.__updateTriggerCalls = [];
    globalThis.__enableTriggerCalls = [];
    globalThis.__disableTriggerCalls = [];
    globalThis.__rotateTriggerCalls = [];
    globalThis.__triggerFixtures = [
        {{
            trigger_id: "trigger-feishu-1",
            name: "feishu_main",
            display_name: "Feishu Main",
            source_type: "im",
            status: "enabled",
            public_token: "public-1",
            source_config: {{
                provider: "feishu",
                trigger_rule: "mention_only",
            }},
            target_config: {{
                workspace_id: "default",
            }},
        }},
        {{
            trigger_id: "trigger-webhook-1",
            name: "http_bridge",
            display_name: "HTTP Bridge",
            source_type: "webhook",
            status: "enabled",
            public_token: "webhook-1",
            source_config: {{
                provider: "custom",
            }},
            target_config: {{
                workspace_id: "default",
            }},
        }},
    ];
    globalThis.__environmentFixtures = {{
        app: [
            {{
                key: "FEISHU_APP_ID",
                value: "cli_existing",
                scope: "app",
                value_kind: "string",
            }},
        ],
        system: [],
    }};
    globalThis.document = {{
        getElementById(id) {{
            return elements.get(id) || null;
        }},
        addEventListener() {{
            return undefined;
        }},
    }};
    globalThis.window = {{
        location: {{
            origin: "https://example.test",
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
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
