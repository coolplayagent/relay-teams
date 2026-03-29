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


def test_orchestration_list_loads_when_role_options_fail(tmp_path: Path) -> None:
    payload = _run_orchestration_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindOrchestrationSettingsHandlers, loadOrchestrationSettingsPanel } from "./orchestrationSettings.mjs";

installGlobals(createElements());
globalThis.__roleConfigOptionsErrorMessage = "System roles unavailable.";
bindOrchestrationSettingsHandlers();
await loadOrchestrationSettingsPanel();

console.log(JSON.stringify({
    listHtml: document.getElementById("orchestration-preset-list").innerHTML,
    listDisplay: document.getElementById("orchestration-preset-list").style.display,
    editorDisplay: document.getElementById("orchestration-editor-panel").style.display,
}));
""".strip(),
    )

    assert "Default Orchestration" in cast(str, payload["listHtml"])
    assert "Shipping Orchestration" in cast(str, payload["listHtml"])
    assert payload["listDisplay"] == "block"
    assert payload["editorDisplay"] == "none"


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
    mock_i18n_path = tmp_path / "mockI18n.mjs"
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
    if (globalThis.__roleConfigOptionsErrorMessage) {
        throw new Error(globalThis.__roleConfigOptionsErrorMessage);
    }
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
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.orchestration.empty_title": "No orchestrations",
    "settings.orchestration.empty_copy": "Add an orchestration to choose roles and orchestration-specific coordinator instructions.",
    "settings.orchestration.default_badge": "Default",
    "settings.orchestration.no_description": "No description",
    "settings.orchestration.edit": "Edit",
    "settings.orchestration.no_roles_title": "No Roles Available",
    "settings.orchestration.no_roles_message": "Create at least one normal role before adding an orchestration.",
    "settings.orchestration.new_name": "New Orchestration",
    "settings.orchestration.fallback_name": "Orchestration",
    "settings.orchestration.file_meta_default": "Orchestration configuration",
    "settings.orchestration.file_meta_existing": "Orchestration: {orchestration_id}",
    "settings.orchestration.file_meta_new": "New orchestration",
    "settings.orchestration.field.id": "Orchestration ID",
    "settings.orchestration.field.name": "Orchestration Name",
    "settings.orchestration.field.description": "Description",
    "settings.orchestration.field.default": "Set as default orchestration",
    "settings.orchestration.allowed_roles": "Allowed Roles",
    "settings.orchestration.prompt_title": "Orchestration Prompt",
    "settings.orchestration.prompt_placeholder": "Explain how Coordinator should split work, choose roles, and drive work to completion.",
    "settings.orchestration.no_roles_available": "No normal roles available.",
    "settings.orchestration.saved_title": "Orchestration Saved",
    "settings.orchestration.saved_message_detail": "Orchestration settings were saved.",
    "settings.orchestration.save_failed_title": "Save Failed",
    "settings.orchestration.save_failed_detail": "Failed to save orchestration settings.",
    "settings.orchestration.delete_title": "Delete Orchestration",
    "settings.orchestration.delete_message": "Delete orchestration \\"{name}\\"?",
    "settings.orchestration.required_title": "Orchestration Required",
    "settings.orchestration.required_message": "At least one orchestration must remain configured.",
    "settings.orchestration.deleted_title": "Orchestration Deleted",
    "settings.orchestration.deleted_message_detail": "The orchestration was deleted.",
    "settings.orchestration.delete_failed_title": "Delete Failed",
    "settings.orchestration.delete_failed_detail": "Failed to delete orchestration.",
    "settings.orchestration.role_count": "Roles: {count}",
    "settings.orchestration.no_current_edit": "No orchestration is currently being edited.",
    "settings.orchestration.id_required": "Orchestration ID is required.",
    "settings.orchestration.name_required": "Orchestration name is required.",
    "settings.orchestration.role_required": "At least one role is required.",
    "settings.orchestration.prompt_required": "Orchestration prompt is required.",
    "settings.orchestration.default_required": "Default orchestration is required.",
    "settings.orchestration.default_existing_required": "Default orchestration must match an existing orchestration.",
    "settings.orchestration.ids_unique": "Orchestration IDs must be unique.",
    "settings.orchestration.load_failed_title": "Load failed",
    "settings.orchestration.load_failed_message": "Unable to load orchestration settings.",
    "settings.action.delete": "Delete",
    "settings.action.cancel": "Cancel",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
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
    globalThis.__roleConfigOptionsErrorMessage = "";
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
