# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from agent_teams.shared_types.json_types import JsonObject


def test_settings_modal_uses_flat_content_stacks_and_switches_tabs(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

const tabs = document.querySelectorAll(".settings-tab");
const notificationsTab = tabs.find(tab => tab.dataset.tab === "notifications");
await notificationsTab.onclick();

console.log(JSON.stringify({
    modalClassName: document.getElementById("settings-modal").className,
    modalDisplay: document.getElementById("settings-modal").style.display,
    modalHtml: document.getElementById("settings-modal").innerHTML,
    panelTitle: document.getElementById("settings-panel-title").textContent,
    modelPanelDisplay: document.getElementById("model-panel").style.display,
    notificationsPanelDisplay: document.getElementById("notifications-panel").style.display,
    loadCalls: globalThis.__loadCalls,
}));
""".strip(),
    )

    modal_html = cast(str, payload["modalHtml"])
    load_calls = cast(JsonObject, payload["loadCalls"])
    assert "settings-content-frame" not in modal_html
    assert "settings-content-stack" in modal_html
    assert "settings-model-stack" in modal_html
    assert "status-stack" in modal_html
    assert "settings-actions-bar" in modal_html
    assert "settings-tab-desc" not in modal_html
    assert (
        "Runtime configuration for models, notifications, and extensions."
        not in modal_html
    )
    assert "Roles" in modal_html
    assert "Providers, endpoints, sampling" not in modal_html
    assert "Browser and toast delivery rules" not in modal_html
    assert "Loaded servers and reload actions" not in modal_html
    assert "Registry state and refresh" not in modal_html
    assert "Each profile is saved server-side" not in modal_html
    assert (
        "Shows the server names currently loaded into the runtime registry."
        not in modal_html
    )
    assert (
        "Lists the skills discovered by the runtime and lets you reload the registry."
        not in modal_html
    )
    assert "notifications-actions" not in modal_html
    assert payload["modalDisplay"] == "flex"
    assert "settings-modal-visible" in str(payload["modalClassName"])
    assert payload["panelTitle"] == "Notifications"
    assert payload["modelPanelDisplay"] == "none"
    assert payload["notificationsPanelDisplay"] == "block"
    assert load_calls == {
        "model": 1,
        "roles": 0,
        "notifications": 1,
        "mcp": 0,
        "skills": 0,
    }


def test_settings_panel_actions_use_primary_buttons_for_add_and_reload(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

const tabs = document.querySelectorAll(".settings-tab");
const rolesTab = tabs.find(tab => tab.dataset.tab === "roles");
const notificationsTab = tabs.find(tab => tab.dataset.tab === "notifications");
const mcpTab = tabs.find(tab => tab.dataset.tab === "mcp");
const skillsTab = tabs.find(tab => tab.dataset.tab === "skills");

const modelAddDisplay = document.getElementById("add-profile-btn").style.display;
await rolesTab.onclick();
const roleAddDisplay = document.getElementById("add-role-btn").style.display;
await notificationsTab.onclick();
const notificationsSaveDisplay = document.getElementById("save-notifications-btn").style.display;
await mcpTab.onclick();
const mcpReloadDisplay = document.getElementById("reload-mcp-btn").style.display;
await skillsTab.onclick();
const skillsReloadDisplay = document.getElementById("reload-skills-btn").style.display;

console.log(JSON.stringify({
    modelAddDisplay,
    roleAddDisplay,
    notificationsSaveDisplay,
    mcpReloadDisplay,
    skillsReloadDisplay,
}));
""".strip(),
    )

    assert payload["modelAddDisplay"] == "inline-flex"
    assert payload["roleAddDisplay"] == "inline-flex"
    assert payload["notificationsSaveDisplay"] == "inline-flex"
    assert payload["mcpReloadDisplay"] == "inline-flex"
    assert payload["skillsReloadDisplay"] == "inline-flex"


def test_settings_content_stack_does_not_draw_duplicate_top_divider() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")
    start = components_css.index(".settings-content-stack {")
    end = components_css.index(".settings-model-stack {", start)
    stack_rule = components_css[start:end]

    assert ".settings-content-stack {" in stack_rule
    assert "border-top: 1px solid var(--settings-divider);" not in stack_rule


def test_settings_layout_uses_scrolling_body_with_footer_actions() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert ".settings-main {" in components_css
    assert "overflow: hidden;" in components_css
    assert ".settings-modal-content {" in components_css
    assert "width: min(1240px, 96vw);" in components_css
    assert "height: min(90vh, 960px);" in components_css
    assert "min-height: 760px;" in components_css
    assert ".settings-body {" in components_css
    assert "overflow-y: auto;" in components_css
    assert ".settings-actions-bar {" in components_css
    assert ".settings-panel {" in components_css
    assert "height: 100%;" in components_css
    assert ".settings-action {" in components_css
    assert ".settings-panel-actions-group {" in components_css
    assert ".settings-panel-actions-group-end {" in components_css
    assert ".settings-sidebar::-webkit-scrollbar {" in components_css
    assert ".settings-body::-webkit-scrollbar {" in components_css
    assert ".profiles-list::-webkit-scrollbar {" in components_css


def test_model_profile_editor_labels_max_output_tokens_and_uses_short_footer_labels(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

console.log(JSON.stringify({
    modalHtml: document.getElementById("settings-modal").innerHTML,
}));
""".strip(),
    )

    modal_html = cast(str, payload["modalHtml"])
    assert "Max Output Tokens" in modal_html
    assert "Max Tokens</label>" not in modal_html
    assert ">Test</button>" in modal_html
    assert ">Validate</button>" in modal_html
    assert ">Save Role</button>" not in modal_html
    assert ">Save Notifications</button>" not in modal_html
    assert "notification-toggle-check" in modal_html
    assert "notification-toggle-label" in modal_html


def test_settings_action_button_order_keeps_cancel_on_far_right() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    actions_html_start = source_text.index('<div class="settings-panel-actions"')
    actions_html_end = source_text.index(
        "</div>\n                </div>", actions_html_start
    )
    actions_html = source_text[actions_html_start:actions_html_end]
    assert "settings-panel-actions-group-start" in actions_html
    assert "settings-panel-actions-group-end" in actions_html
    assert actions_html.index('id="test-profile-btn"') < actions_html.index(
        'id="save-profile-btn"'
    )
    assert actions_html.index('id="save-profile-btn"') < actions_html.index(
        'id="cancel-profile-btn"'
    )
    assert actions_html.index('id="validate-role-btn"') < actions_html.index(
        'id="save-role-btn"'
    )
    assert actions_html.index('id="save-role-btn"') < actions_html.index(
        'id="cancel-role-btn"'
    )


def _run_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    )

    mock_model_profiles_path = tmp_path / "mockModelProfiles.mjs"
    mock_notifications_path = tmp_path / "mockNotifications.mjs"
    mock_roles_settings_path = tmp_path / "mockRolesSettings.mjs"
    mock_system_status_path = tmp_path / "mockSystemStatus.mjs"
    module_under_test_path = tmp_path / "index.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_model_profiles_path.write_text(
        """
export function bindModelProfileHandlers() {
    globalThis.__bindCalls.model += 1;
}

export async function loadModelProfilesPanel() {
    globalThis.__loadCalls.model += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_notifications_path.write_text(
        """
export function bindNotificationSettingsHandlers() {
    globalThis.__bindCalls.notifications += 1;
}

export async function loadNotificationSettingsPanel() {
    globalThis.__loadCalls.notifications += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_roles_settings_path.write_text(
        """
export function bindRoleSettingsHandlers() {
    globalThis.__bindCalls.roles += 1;
}

export async function loadRoleSettingsPanel() {
    globalThis.__loadCalls.roles += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_system_status_path.write_text(
        """
export function bindSystemStatusHandlers() {
    globalThis.__bindCalls.system += 1;
}

export async function loadMcpStatusPanel() {
    globalThis.__loadCalls.mcp += 1;
}

export async function loadSkillsStatusPanel() {
    globalThis.__loadCalls.skills += 1;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./modelProfiles.js", "./mockModelProfiles.mjs")
        .replace("./notifications.js", "./mockNotifications.mjs")
        .replace("./rolesSettings.js", "./mockRolesSettings.mjs")
        .replace("./systemStatus.js", "./mockSystemStatus.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createClassList(element) {{
    const classes = new Set();

    function sync() {{
        element.className = Array.from(classes).join(" ");
    }}

    return {{
        add(...tokens) {{
            tokens.filter(Boolean).forEach(token => classes.add(token));
            sync();
        }},
        remove(...tokens) {{
            tokens.forEach(token => classes.delete(token));
            sync();
        }},
        toggle(token, force) {{
            const shouldAdd = force === undefined ? !classes.has(token) : Boolean(force);
            if (shouldAdd) {{
                classes.add(token);
            }} else {{
                classes.delete(token);
            }}
            sync();
            return shouldAdd;
        }},
        contains(token) {{
            return classes.has(token);
        }},
        resetFromString(value) {{
            classes.clear();
            String(value || "")
                .split(/\\s+/)
                .filter(Boolean)
                .forEach(token => classes.add(token));
            sync();
        }},
    }};
}}

function createElement(tagName = "div") {{
    const element = {{
        tagName,
        id: "",
        style: {{}},
        dataset: {{}},
        children: [],
        textContent: "",
        onclick: null,
        parentNode: null,
        appendChild(child) {{
            child.parentNode = this;
            this.children.push(child);
        }},
        querySelectorAll(selector) {{
            if (selector !== ".settings-action") {{
                return [];
            }}
            const matches = [];
            for (const match of this.innerHTML.matchAll(/class="[^"]*settings-action[^"]*"[^>]*id="([^"]+)"/g)) {{
                const child = createElement("button");
                child.id = match[1];
                matches.push(child);
            }}
            return matches;
        }},
    }};

    element.classList = createClassList(element);
    let html = "";
    Object.defineProperty(element, "innerHTML", {{
        get() {{
            return html;
        }},
        set(value) {{
            html = String(value);
        }},
    }});
    Object.defineProperty(element, "className", {{
        get() {{
            return element.__className || "";
        }},
        set(value) {{
            element.__className = String(value || "");
        }},
    }});

    return element;
}}

function createDocument() {{
    const elements = new Map();
    const tabs = [];
    const panels = [];
    const body = createElement("body");

    function registerElement(id, element) {{
        if (!id) {{
            return;
        }}
        element.id = id;
        if (!elements.has(id)) {{
            elements.set(id, element);
        }}
    }}

    function parseInnerHtml(target) {{
        tabs.length = 0;
        panels.length = 0;

        const html = target.innerHTML;

        for (const match of html.matchAll(/id="([^"]+)"/g)) {{
            registerElement(match[1], createElement());
        }}

        for (const match of html.matchAll(/class="settings-tab([^"]*)" data-tab="([^"]+)"/g)) {{
            const tab = createElement("button");
            tab.dataset.tab = match[2];
            tab.classList.resetFromString(`settings-tab${{match[1]}}`);
            tabs.push(tab);
        }}

        for (const match of html.matchAll(/class="settings-panel" id="([^"]+)"(?: style="display:none;")?/g)) {{
            const panel = elements.get(match[1]) || createElement();
            panel.id = match[1];
            panel.style.display = html.includes(`id="${{match[1]}}" style="display:none;"`) ? "none" : "block";
            panels.push(panel);
            elements.set(match[1], panel);
        }}
    }}

    const originalAppendChild = body.appendChild.bind(body);
    body.appendChild = (child) => {{
        originalAppendChild(child);
        registerElement(child.id, child);
        parseInnerHtml(child);
    }};

        return {{
            body,
            createElement,
            getElementById(id) {{
                return elements.get(id) || null;
            }},
        querySelectorAll(selector) {{
            if (selector === ".settings-tab") {{
                return tabs;
            }}
            if (selector === ".settings-panel") {{
                return panels;
            }}
            return [];
        }},
    }};
}}

globalThis.__bindCalls = {{
    model: 0,
    roles: 0,
    notifications: 0,
    system: 0,
}};
globalThis.__loadCalls = {{
    model: 0,
    roles: 0,
    notifications: 0,
    mcp: 0,
    skills: 0,
}};

globalThis.document = createDocument();
globalThis.window = {{}};

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
