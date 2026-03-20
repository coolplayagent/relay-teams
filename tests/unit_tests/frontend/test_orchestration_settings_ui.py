# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from pydantic import JsonValue


def test_orchestration_add_cancel_does_not_pollute_list(tmp_path: Path) -> None:
    payload = _run_orchestration_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindOrchestrationSettingsHandlers, loadOrchestrationSettingsPanel } from "./orchestrationSettings.mjs";

installGlobals(createElements());
bindOrchestrationSettingsHandlers();
await loadOrchestrationSettingsPanel();

const initialListHtml = document.getElementById("orchestration-preset-list").innerHTML;
await document.getElementById("add-orchestration-preset-btn").onclick();
await document.getElementById("cancel-orchestration-btn").onclick();

console.log(JSON.stringify({
    initialListHtml,
    finalListHtml: document.getElementById("orchestration-preset-list").innerHTML,
    listDisplay: document.getElementById("orchestration-preset-list").style.display,
    editorDisplay: document.getElementById("orchestration-editor-panel").style.display,
    saveCalls: globalThis.__saveCalls,
}));
""".strip(),
    )

    assert "Default Orchestration" in cast(str, payload["initialListHtml"])
    assert "New Orchestration" not in cast(str, payload["finalListHtml"])
    assert payload["listDisplay"] == "block"
    assert payload["editorDisplay"] == "none"
    assert payload["saveCalls"] == []


def test_orchestration_delete_confirms_and_persists_immediately(tmp_path: Path) -> None:
    payload = _run_orchestration_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindOrchestrationSettingsHandlers, loadOrchestrationSettingsPanel } from "./orchestrationSettings.mjs";

installGlobals(createElements());
bindOrchestrationSettingsHandlers();
await loadOrchestrationSettingsPanel();

await document.getElementById("orchestration-preset-list").querySelectorAll(".orchestration-edit-btn")[1].onclick({ stopPropagation() {} });
await document.getElementById("delete-orchestration-preset-btn").onclick();

console.log(JSON.stringify({
    saveCalls: globalThis.__saveCalls,
    confirmCalls: globalThis.__confirmCalls,
    listHtml: document.getElementById("orchestration-preset-list").innerHTML,
    listDisplay: document.getElementById("orchestration-preset-list").style.display,
}));
""".strip(),
    )

    save_calls = cast(list[dict[str, JsonValue]], payload["saveCalls"])
    confirm_calls = cast(list[dict[str, JsonValue]], payload["confirmCalls"])
    assert len(confirm_calls) == 1
    assert confirm_calls[0]["title"] == "Delete Orchestration"
    assert len(save_calls) == 1
    saved_config = cast(dict[str, JsonValue], save_calls[0]["config"])
    assert saved_config["default_orchestration_preset_id"] == "default"
    assert len(cast(list[JsonValue], saved_config["presets"])) == 1
    assert "Shipping Orchestration" not in cast(str, payload["listHtml"])
    assert payload["listDisplay"] == "block"


def _run_orchestration_settings_script(
    tmp_path: Path, runner_source: str
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "orchestrationSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "orchestrationSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
let orchestrationConfig = {
    default_orchestration_preset_id: "default",
    presets: [
        {
            preset_id: "default",
            name: "Default Orchestration",
            description: "General routing.",
            role_ids: ["Writer", "Reviewer"],
            orchestration_prompt: "Route by capability.",
        },
        {
            preset_id: "shipping",
            name: "Shipping Orchestration",
            description: "Release work.",
            role_ids: ["Writer"],
            orchestration_prompt: "Use Writer for outward communication.",
        },
    ],
};

export async function fetchOrchestrationConfig() {
    return JSON.parse(JSON.stringify(orchestrationConfig));
}

export async function saveOrchestrationConfig(config) {
    globalThis.__saveCalls.push({ config });
    orchestrationConfig = JSON.parse(JSON.stringify(config));
    return { status: "ok" };
}

export async function fetchRoleConfigs() {
    return [
        { role_id: "Writer", name: "Writer" },
        { role_id: "Reviewer", name: "Reviewer" },
        { role_id: "Coordinator", name: "Coordinator" },
        { role_id: "MainAgent", name: "Main Agent" },
    ];
}

export async function fetchRoleConfigOptions() {
    return {
        coordinator_role_id: "Coordinator",
        main_agent_role_id: "MainAgent",
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

export async function showConfirmDialog(payload) {
    globalThis.__confirmCalls.push(payload);
    return globalThis.__confirmResult;
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
function createClassList(element) {{
    const classes = new Set();
    return {{
        add(token) {{
            classes.add(token);
            element.className = Array.from(classes).join(" ");
        }},
        remove(token) {{
            classes.delete(token);
            element.className = Array.from(classes).join(" ");
        }},
        toggle(token, force) {{
            const shouldAdd = force === undefined ? !classes.has(token) : Boolean(force);
            if (shouldAdd) {{
                classes.add(token);
            }} else {{
                classes.delete(token);
            }}
            element.className = Array.from(classes).join(" ");
        }},
    }};
}}

function createElement(initialDisplay = "block") {{
    let html = "";
    let cachedRoleRecords = [];
    let cachedEditButtons = [];
    let cachedCheckboxes = [];

    function refreshCaches(source) {{
        cachedRoleRecords = [];
        cachedEditButtons = [];
        cachedCheckboxes = [];

        const recordPattern = /class="role-record([^"]*)" data-orchestration-id="([^"]+)"/g;
        let recordMatch = recordPattern.exec(source);
        while (recordMatch) {{
            cachedRoleRecords.push({{
                dataset: {{ orchestrationId: recordMatch[2] }},
                onclick: null,
                className: `role-record${{recordMatch[1]}}`,
            }});
            recordMatch = recordPattern.exec(source);
        }}

        const editPattern = /class="[^"]*orchestration-edit-btn[^"]*" data-orchestration-id="([^"]+)"/g;
        let editMatch = editPattern.exec(source);
        while (editMatch) {{
            cachedEditButtons.push({{
                dataset: {{ orchestrationId: editMatch[1] }},
                onclick: null,
            }});
            editMatch = editPattern.exec(source);
        }}

        const checkboxPattern = /<input type="checkbox" data-role-id="([^"]+)"( checked)?>/g;
        let checkboxMatch = checkboxPattern.exec(source);
        while (checkboxMatch) {{
            cachedCheckboxes.push({{
                dataset: {{ roleId: checkboxMatch[1] }},
                checked: Boolean(checkboxMatch[2]),
                onchange: null,
            }});
            checkboxMatch = checkboxPattern.exec(source);
        }}
    }}

    const element = {{
        style: {{ display: initialDisplay }},
        value: "",
        checked: false,
        textContent: "",
        className: "",
        dataset: {{}},
        onclick: null,
        oninput: null,
        innerHTML: "",
        focus() {{
            return undefined;
        }},
        querySelectorAll(selector) {{
            if (selector === ".role-record") {{
                return cachedRoleRecords;
            }}
            if (selector === ".orchestration-edit-btn") {{
                return cachedEditButtons;
            }}
            if (selector === 'input[type="checkbox"]') {{
                return cachedCheckboxes;
            }}
            return [];
        }},
    }};

    Object.defineProperty(element, "innerHTML", {{
        get() {{
            return html;
        }},
        set(value) {{
            html = String(value);
            refreshCaches(html);
            if (globalThis.__syncEditorFields && element.id === "orchestration-preset-editor") {{
                globalThis.__syncEditorFields(html);
            }}
        }},
    }});

    element.classList = createClassList(element);
    return element;
}}

function createElements() {{
    const elements = new Map([
        ["orchestration-preset-list", createElement("block")],
        ["orchestration-editor-panel", createElement("none")],
        ["orchestration-editor-form", createElement("none")],
        ["orchestration-editor-empty", createElement("none")],
        ["orchestration-preset-editor", createElement("block")],
        ["delete-orchestration-preset-btn", createElement("block")],
        ["save-orchestration-btn", createElement("block")],
        ["cancel-orchestration-btn", createElement("block")],
        ["add-orchestration-preset-btn", createElement("block")],
        ["orchestration-editor-status", createElement("none")],
        ["orchestration-file-meta", createElement("block")],
        ["orchestration-id-input", createElement("block")],
        ["orchestration-name-input", createElement("block")],
        ["orchestration-description-input", createElement("block")],
        ["orchestration-default-input", createElement("block")],
        ["orchestration-role-picker", createElement("block")],
        ["orchestration-prompt-input", createElement("block")],
    ]);

    for (const [id, element] of elements.entries()) {{
        element.id = id;
    }}
    return elements;
}}

function installGlobals(elements) {{
    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
        dispatchEvent() {{
            return undefined;
        }},
    }};
    globalThis.CustomEvent = class CustomEvent {{
        constructor(name) {{
            this.type = name;
        }}
    }};
    globalThis.__feedbackNotifications = [];
    globalThis.__confirmCalls = [];
    globalThis.__confirmResult = true;
    globalThis.__saveCalls = [];
    globalThis.__syncEditorFields = html => {{
        const idMatch = html.match(/id="orchestration-id-input" value="([^"]*)"/);
        const nameMatch = html.match(/id="orchestration-name-input" value="([^"]*)"/);
        const descMatch = html.match(/id="orchestration-description-input" value="([^"]*)"/);
        const promptMatch = html.match(/<textarea id="orchestration-prompt-input"[^>]*>([\\s\\S]*?)<\\/textarea>/);
        const defaultMatch = html.match(/id="orchestration-default-input"( checked)?/);
        elements.get("orchestration-id-input").value = idMatch ? idMatch[1] : "";
        elements.get("orchestration-name-input").value = nameMatch ? nameMatch[1] : "";
        elements.get("orchestration-description-input").value = descMatch ? descMatch[1] : "";
        elements.get("orchestration-prompt-input").value = promptMatch ? promptMatch[1] : "";
        elements.get("orchestration-default-input").checked = Boolean(defaultMatch && defaultMatch[1]);

        const rolePicker = elements.get("orchestration-role-picker");
        rolePicker.innerHTML = html;
    }};
}};

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
            "Node runner failed:\\n"
            f"STDOUT:\\n{completed.stdout}\\n"
            f"STDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
