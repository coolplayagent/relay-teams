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
    assert "Each profile is saved server-side" not in modal_html
    assert (
        "Shows the server names currently loaded into the runtime registry."
        not in modal_html
    )
    assert (
        "Lists the skills discovered by the runtime and lets you reload the registry."
        not in modal_html
    )
    assert payload["modalDisplay"] == "flex"
    assert "settings-modal-visible" in str(payload["modalClassName"])
    assert payload["panelTitle"] == "Notifications"
    assert payload["modelPanelDisplay"] == "none"
    assert payload["notificationsPanelDisplay"] == "block"
    assert load_calls == {
        "model": 1,
        "notifications": 1,
        "mcp": 0,
        "skills": 0,
    }


def _run_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    )

    mock_model_profiles_path = tmp_path / "mockModelProfiles.mjs"
    mock_notifications_path = tmp_path / "mockNotifications.mjs"
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
    notifications: 0,
    system: 0,
}};
globalThis.__loadCalls = {{
    model: 0,
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
