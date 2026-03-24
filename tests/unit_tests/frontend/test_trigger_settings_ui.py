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
    expandedPlatformHtml: document.getElementById("trigger-platform-list").innerHTML,
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
    expanded_platform_html = cast(str, payload["expandedPlatformHtml"])
    assert payload["notifications"] == []
    assert "Feishu" in platform_html
    assert "1 triggers" in platform_html
    assert "Credentials Missing" in platform_html
    assert "feishu_main" in expanded_platform_html
    assert "http_bridge" not in expanded_platform_html
    assert payload["platformDisplay"] == "block"
    assert payload["detailDisplay"] == "none"
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"
    assert payload["actionsBarDisplay"] == "flex"


def test_trigger_settings_adds_feishu_trigger_with_embedded_bot_config(
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

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("add-trigger-btn").onclick();

document.getElementById("feishu-app-id-input").value = "cli_demo";
document.getElementById("feishu-app-id-input").oninput();
document.getElementById("feishu-app-name-input").value = "Agent Teams Bot";
document.getElementById("feishu-app-name-input").oninput();
document.getElementById("feishu-app-secret-input").value = "secret-demo";
document.getElementById("feishu-app-secret-input").oninput();
document.getElementById("feishu-trigger-name-input").value = "feishu_ops";
document.getElementById("feishu-trigger-name-input").oninput();
document.getElementById("feishu-trigger-display-name-input").value = "Feishu Ops";
document.getElementById("feishu-trigger-display-name-input").oninput();
document.getElementById("feishu-trigger-workspace-id-input").value = "workspace-ops";
document.getElementById("feishu-trigger-workspace-id-input").onchange();
document.getElementById("feishu-session-mode-input").value = "orchestration";
document.getElementById("feishu-session-mode-input").onchange();
document.getElementById("feishu-orchestration-preset-id-input").value = "default";
document.getElementById("feishu-orchestration-preset-id-input").onchange();
document.getElementById("feishu-normal-root-role-id-input").value = "MainAgent";
document.getElementById("feishu-normal-root-role-id-input").onchange();
document.getElementById("feishu-trigger-yolo-input").value = "false";
document.getElementById("feishu-trigger-yolo-input").onchange();
document.getElementById("feishu-trigger-thinking-enabled-input").value = "true";
document.getElementById("feishu-trigger-thinking-enabled-input").onchange();
document.getElementById("feishu-thinking-effort-input").value = "high";
document.getElementById("feishu-thinking-effort-input").onchange();
document.getElementById("feishu-trigger-enabled-input").value = "false";
document.getElementById("feishu-trigger-enabled-input").onchange();

await document.getElementById("save-trigger-btn").onclick();

console.log(JSON.stringify({
    notifications,
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
    create_calls = cast(list[dict[str, JsonValue]], payload["createCalls"])
    assert payload["updateCalls"] == []
    assert payload["enableCalls"] == []
    assert payload["disableCalls"] == []
    assert len(create_calls) == 1
    create_payload = cast(dict[str, JsonValue], create_calls[0]["payload"])
    assert create_payload["name"] == "feishu_ops"
    assert create_payload["display_name"] == "Feishu Ops"
    assert create_payload["source_type"] == "im"
    assert create_payload["enabled"] is False
    assert create_payload["source_config"] == {
        "provider": "feishu",
        "trigger_rule": "mention_only",
        "app_id": "cli_demo",
        "app_name": "Agent Teams Bot",
    }
    assert create_payload["target_config"] == {
        "workspace_id": "workspace-ops",
        "session_mode": "orchestration",
        "orchestration_preset_id": "default",
        "yolo": False,
        "thinking": {"enabled": True, "effort": "high"},
    }
    assert create_payload["secret_config"] == {
        "app_secret": "secret-demo",
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


def test_trigger_settings_updates_existing_trigger_without_create_only_fields(
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
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-record-edit-btn")[0].onclick({ stopPropagation() {} });

document.getElementById("feishu-trigger-name-input").value = "feishu_main";
document.getElementById("feishu-trigger-name-input").oninput();
document.getElementById("feishu-trigger-display-name-input").value = "Feishu Main Updated";
document.getElementById("feishu-trigger-display-name-input").oninput();
document.getElementById("feishu-app-id-input").value = "cli_existing";
document.getElementById("feishu-app-id-input").oninput();
document.getElementById("feishu-app-name-input").value = "Agent Teams Bot";
document.getElementById("feishu-app-name-input").oninput();
document.getElementById("feishu-trigger-workspace-id-input").value = "default";
document.getElementById("feishu-trigger-workspace-id-input").onchange();
document.getElementById("feishu-trigger-enabled-input").value = "false";
document.getElementById("feishu-trigger-enabled-input").onchange();

await document.getElementById("save-trigger-btn").onclick();

console.log(JSON.stringify({
    updateCalls: globalThis.__updateTriggerCalls,
    enableCalls: globalThis.__enableTriggerCalls,
    disableCalls: globalThis.__disableTriggerCalls,
}));
""".strip(),
    )

    update_calls = cast(list[dict[str, JsonValue]], payload["updateCalls"])
    assert len(update_calls) == 1
    update_payload = cast(dict[str, JsonValue], update_calls[0]["payload"])
    assert "source_type" not in update_payload
    assert "enabled" not in update_payload
    assert update_payload["display_name"] == "Feishu Main Updated"
    assert payload["enableCalls"] == []
    assert payload["disableCalls"] == ["trigger-feishu-1"]


def _run_trigger_settings_script(
    tmp_path: Path,
    runner_source: str,
) -> dict[str, object]:
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

export async function fetchWorkspaces() {
    return globalThis.__workspaceFixtures;
}

export async function fetchRoleConfigOptions() {
    return globalThis.__roleOptionsFixture;
}

export async function fetchOrchestrationConfig() {
    return globalThis.__orchestrationFixture;
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
    "settings.triggers.feishu_detail_copy": "Manage Feishu bot triggers.",
    "settings.triggers.records": "Triggers",
    "settings.triggers.records_copy": "Provider-specific trigger records.",
    "settings.triggers.none": "No Feishu triggers",
    "settings.triggers.none_copy": "Add a Feishu trigger.",
    "settings.triggers.editor": "Trigger Editor",
    "settings.triggers.editing_existing": "Editing existing trigger",
    "settings.triggers.editing_new": "New trigger",
    "settings.triggers.bot_configuration": "Bot Configuration",
    "settings.triggers.session_configuration": "Session Configuration",
    "settings.triggers.trigger_name": "Trigger Name",
    "settings.triggers.display_name": "Display Name",
    "settings.triggers.workspace": "Workspace ID",
    "settings.triggers.rule": "Trigger Rule",
    "settings.triggers.mode": "Session Mode",
    "settings.triggers.normal_root_role_id": "Normal Root Role",
    "settings.triggers.orchestration_preset_id": "Orchestration Preset",
    "settings.triggers.thinking_effort": "Thinking Effort",
    "settings.triggers.yolo": "YOLO",
    "settings.triggers.thinking_enabled": "Thinking Enabled",
    "settings.triggers.enable_trigger": "Enable trigger",
    "settings.triggers.saved": "Trigger Settings Saved",
    "settings.triggers.saved_message": "Feishu trigger settings saved.",
    "settings.triggers.save_failed": "Save Failed",
    "settings.triggers.load_failed": "Load Failed",
    "settings.triggers.missing_name": "Trigger name is required.",
    "settings.triggers.missing_workspace": "Workspace ID is required.",
    "settings.triggers.missing_app_id": "App ID is required.",
    "settings.triggers.missing_app_name": "App name is required.",
    "settings.triggers.missing_app_secret": "App secret is required.",
    "settings.triggers.missing_orchestration_preset_id": "Preset is required in orchestration mode.",
    "settings.triggers.unnamed": "Unnamed trigger",
    "settings.triggers.feishu_app_id": "Feishu App ID",
    "settings.triggers.feishu_app_id_placeholder": "cli_xxx",
    "settings.triggers.feishu_app_name": "Application Name",
    "settings.triggers.feishu_app_name_placeholder": "Agent Teams Bot",
    "settings.triggers.feishu_app_secret": "Feishu App Secret",
    "settings.triggers.feishu_app_secret_placeholder": "App secret",
    "settings.triggers.secret_keep_placeholder": "Configured. Leave blank to keep current value.",
    "settings.triggers.no_workspaces": "No workspaces",
    "settings.triggers.option_enabled": "Enabled",
    "settings.triggers.option_disabled": "Disabled",
    "settings.triggers.credentials_ready": "Credentials ready",
    "settings.triggers.credentials_missing_count": "{count} credentials missing",
    "composer.mode_normal": "Normal Mode",
    "composer.mode_orchestration": "Orchestrated Mode",
    "composer.no_roles": "No roles",
    "composer.no_presets": "No presets",
    "settings.field.enabled": "Enabled",
    "settings.roles.disabled": "Disabled",
    "settings.roles.edit": "Edit"
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
        type: "text",
        tagName: "INPUT",
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
    const textIds = [
        "settings-actions-bar",
        "add-trigger-btn",
        "save-trigger-btn",
        "cancel-trigger-btn",
        "trigger-platform-list",
        "trigger-provider-detail-panel",
        "trigger-provider-detail",
        "trigger-editor-status",
        "trigger-provider-back-btn",
        "feishu-app-id-input",
        "feishu-app-name-input",
        "feishu-app-secret-input",
        "feishu-trigger-name-input",
        "feishu-trigger-display-name-input",
        "feishu-normal-role-field",
        "feishu-preset-field",
    ];
    const elements = new Map();
    textIds.forEach(id => elements.set(id, createElement(id === "settings-actions-bar" ? "flex" : "none")));
    ["trigger-platform-list", "trigger-provider-detail", "feishu-trigger-rule-input", "feishu-session-mode-input", "feishu-thinking-effort-input", "feishu-trigger-workspace-id-input", "feishu-normal-root-role-id-input", "feishu-orchestration-preset-id-input", "feishu-trigger-enabled-input", "feishu-trigger-yolo-input", "feishu-trigger-thinking-enabled-input"].forEach(id => {{
        const element = elements.get(id) || createElement();
        element.tagName = "SELECT";
        element.style.display = id === "trigger-platform-list" ? "block" : "none";
        elements.set(id, element);
    }});
    return elements;
}}

function installGlobals(elements, notifications) {{
    globalThis.__feedbackNotifications = notifications;
    globalThis.__createTriggerCalls = [];
    globalThis.__updateTriggerCalls = [];
    globalThis.__enableTriggerCalls = [];
    globalThis.__disableTriggerCalls = [];
    globalThis.__workspaceFixtures = [
        {{
            workspace_id: "default",
            root_path: "/work/default"
        }},
        {{
            workspace_id: "workspace-ops",
            root_path: "/work/ops"
        }}
    ];
    globalThis.__roleOptionsFixture = {{
        normal_mode_roles: [
            {{ role_id: "MainAgent", name: "Main Agent" }},
            {{ role_id: "SpecCoder", name: "Spec Coder" }}
        ]
    }};
    globalThis.__orchestrationFixture = {{
        default_orchestration_preset_id: "default",
        presets: [
            {{ preset_id: "default", name: "Default Preset" }},
            {{ preset_id: "ops", name: "Ops Preset" }}
        ]
    }};
    globalThis.__triggerFixtures = [
        {{
            trigger_id: "trigger-feishu-1",
            name: "feishu_main",
            display_name: "Feishu Main",
            source_type: "im",
            status: "enabled",
            source_config: {{
                provider: "feishu",
                trigger_rule: "mention_only",
                app_id: "cli_existing",
                app_name: "Agent Teams Bot"
            }},
            target_config: {{
                workspace_id: "default",
                session_mode: "normal",
                yolo: true,
                thinking: {{ enabled: false, effort: "medium" }}
            }},
            secret_status: {{
                app_secret_configured: false
            }}
        }},
        {{
            trigger_id: "trigger-webhook-1",
            name: "http_bridge",
            display_name: "HTTP Bridge",
            source_type: "webhook",
            status: "enabled",
            source_config: {{
                provider: "custom"
            }},
            target_config: {{
                workspace_id: "default"
            }}
        }}
    ];
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
