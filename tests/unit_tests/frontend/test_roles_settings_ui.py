# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast


def test_role_settings_panel_switches_roles_and_previews_prompt(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

const initialListHtml = document.getElementById("roles-list").innerHTML;
const editButtons = document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn");
await editButtons[1].onclick({ stopPropagation() {} });
await document.getElementById("role-prompt-preview-tab").onclick();

console.log(JSON.stringify({
    initialListHtml,
    selectedRoleId: document.getElementById("role-id-input").value,
    selectedRoleName: document.getElementById("role-name-input").value,
    selectedRoleDescription: document.getElementById("role-description-input").value,
    memoryEnabled: document.getElementById("role-memory-enabled-input").value,
    dailyMemoryEnabled: document.getElementById("role-memory-daily-enabled-input").value,
    listDisplay: document.getElementById("roles-list").style.display,
    editorDisplay: document.getElementById("role-editor-panel").style.display,
    modelProfileValue: document.getElementById("role-model-profile-input").value,
    modelProfileHtml: document.getElementById("role-model-profile-input").innerHTML,
    promptPreviewDisplay: document.getElementById("role-system-prompt-preview").style.display,
    promptEditorDisplay: document.getElementById("role-system-prompt-input").style.display,
    promptPreviewHtml: document.getElementById("role-system-prompt-preview").innerHTML,
    fetchCalls: globalThis.__fetchRoleConfigCalls,
}));
""".strip(),
    )

    fetch_calls = cast(list[JsonValue], payload["fetchCalls"])
    assert "Writer" in cast(str, payload["initialListHtml"])
    assert "Reviewer" in cast(str, payload["initialListHtml"])
    assert payload["selectedRoleId"] == "reviewer"
    assert payload["selectedRoleName"] == "Reviewer"
    assert payload["selectedRoleDescription"] == "Reviews delivered work."
    assert payload["memoryEnabled"] == "true"
    assert payload["dailyMemoryEnabled"] == "false"
    assert payload["listDisplay"] == "none"
    assert payload["editorDisplay"] == "block"
    assert payload["modelProfileValue"] == "default"
    assert '<option value="default" selected>default</option>' in cast(
        str, payload["modelProfileHtml"]
    )
    assert '<option value="editor">editor</option>' in cast(
        str, payload["modelProfileHtml"]
    )
    assert payload["promptPreviewDisplay"] == "block"
    assert payload["promptEditorDisplay"] == "none"
    assert (
        payload["promptPreviewHtml"] == "<article>Review the delivered work.</article>"
    )
    assert fetch_calls == ["reviewer"]


def test_role_settings_validate_save_and_add_role_use_controlled_options(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn")[0].onclick({ stopPropagation() {} });
const toolOptions = document.getElementById("role-tools-picker").querySelectorAll('input[type="checkbox"]');
toolOptions[1].checked = true;
toolOptions[1].onchange();

document.getElementById("role-model-profile-input").value = "editor";
document.getElementById("role-description-input").value = "Drafts user-facing content with structure.";
document.getElementById("role-memory-enabled-input").value = "false";
document.getElementById("role-memory-daily-enabled-input").value = "true";
document.getElementById("role-system-prompt-input").value = "Write the first draft with structure.";

await document.getElementById("validate-role-btn").onclick();
await document.getElementById("save-role-btn").onclick();

await document.getElementById("add-role-btn").onclick();
document.getElementById("role-id-input").value = "new_role";
document.getElementById("role-name-input").value = "New Role";
document.getElementById("role-description-input").value = "Starts from a blank role.";
document.getElementById("role-version-input").value = "1.0.0";
document.getElementById("role-model-profile-input").value = "default";
document.getElementById("role-system-prompt-input").value = "Start from a blank role.";

const newToolOptions = document.getElementById("role-tools-picker").querySelectorAll('input[type="checkbox"]');
newToolOptions[0].checked = true;
newToolOptions[0].onchange();

await document.getElementById("save-role-btn").onclick();

console.log(JSON.stringify({
    validatePayload: globalThis.__validatePayload,
    firstSavedRoleId: globalThis.__saveCalls[0].roleId,
    firstSavedPayload: globalThis.__saveCalls[0].payload,
    secondSavedRoleId: globalThis.__saveCalls[1].roleId,
    secondSavedPayload: globalThis.__saveCalls[1].payload,
    statusText: document.getElementById("role-editor-status").textContent,
    notifications: globalThis.__feedbackNotifications,
    roleSummaryCalls: globalThis.__fetchRoleConfigsCount,
    fileMeta: document.getElementById("role-file-meta").textContent,
}));
""".strip(),
    )

    validate_payload = cast(dict[str, JsonValue], payload["validatePayload"])
    first_saved_payload = cast(dict[str, JsonValue], payload["firstSavedPayload"])
    second_saved_payload = cast(dict[str, JsonValue], payload["secondSavedPayload"])
    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert validate_payload["source_role_id"] == "writer"
    assert validate_payload["role_id"] == "writer"
    assert (
        validate_payload["description"] == "Drafts user-facing content with structure."
    )
    assert validate_payload["tools"] == ["read_file", "write_file"]
    assert validate_payload["memory_profile"] == {
        "enabled": False,
        "daily_enabled": True,
    }
    assert validate_payload["model_profile"] == "editor"
    assert payload["firstSavedRoleId"] == "writer"
    assert first_saved_payload == validate_payload
    assert payload["secondSavedRoleId"] == "new_role"
    assert second_saved_payload["source_role_id"] is None
    assert second_saved_payload["role_id"] == "new_role"
    assert second_saved_payload["description"] == "Starts from a blank role."
    assert second_saved_payload["tools"] == ["read_file"]
    assert second_saved_payload["memory_profile"] == {
        "enabled": True,
        "daily_enabled": True,
    }
    assert payload["statusText"] == "Saved and validated."
    assert payload["fileMeta"] == "File: new_role.md"
    assert payload["roleSummaryCalls"] == 3
    assert notifications == [
        {
            "title": "Role Validated",
            "message": "writer passed validation.",
            "tone": "success",
        },
        {
            "title": "Role Saved",
            "message": "writer saved and reloaded.",
            "tone": "success",
        },
        {
            "title": "Role Saved",
            "message": "new_role saved and reloaded.",
            "tone": "success",
        },
    ]


def _run_roles_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "rolesSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_markdown_path = tmp_path / "mockMarkdown.mjs"
    module_under_test_path = tmp_path / "rolesSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
const roleRecords = {
    writer: {
        source_role_id: "writer",
        role_id: "writer",
        name: "Writer",
        description: "Drafts user-facing content.",
        version: "1.0.0",
        tools: ["read_file"],
        mcp_servers: [],
        skills: [],
        model_profile: "default",
        memory_profile: { enabled: true, daily_enabled: true },
        system_prompt: "Write the first draft.",
        file_name: "writer.md",
        content: "---\\nrole_id: writer\\n---\\n\\nWrite the first draft.\\n",
    },
    reviewer: {
        source_role_id: "reviewer",
        role_id: "reviewer",
        name: "Reviewer",
        description: "Reviews delivered work.",
        version: "1.0.0",
        tools: ["read_file", "write_file"],
        mcp_servers: ["docs"],
        skills: ["diff"],
        model_profile: "default",
        memory_profile: { enabled: true, daily_enabled: false },
        system_prompt: "Review the delivered work.",
        file_name: "reviewer.md",
        content: "---\\nrole_id: reviewer\\n---\\n\\nReview the delivered work.\\n",
    },
};

export async function fetchRoleConfigs() {
    globalThis.__fetchRoleConfigsCount += 1;
    return Object.values(roleRecords).map(record => ({
        role_id: record.role_id,
        name: record.name,
        description: record.description,
        version: record.version,
        model_profile: record.model_profile,
    }));
}

export async function fetchRoleConfigOptions() {
        return {
            tools: ["read_file", "write_file"],
            mcp_servers: ["docs"],
            skills: ["diff", "time"],
        };
}

export async function fetchModelProfiles() {
    return {
        default: { model: "gpt-4o-mini" },
        editor: { model: "gpt-4.1" },
    };
}

export async function fetchRoleConfig(roleId) {
    globalThis.__fetchRoleConfigCalls.push(roleId);
    return roleRecords[roleId];
}

export async function validateRoleConfig(payload) {
    globalThis.__validatePayload = payload;
    return {
        valid: true,
        role: {
            ...payload,
            source_role_id: payload.source_role_id,
            file_name: `${payload.role_id}.md`,
            content: `---\\nrole_id: ${payload.role_id}\\n---\\n\\n${payload.system_prompt}\\n`,
        },
    };
}

export async function saveRoleConfig(roleId, payload) {
    globalThis.__saveCalls.push({ roleId, payload });
    roleRecords[payload.role_id] = {
        ...payload,
        source_role_id: payload.source_role_id,
        file_name: `${payload.role_id}.md`,
        content: `---\\nrole_id: ${payload.role_id}\\n---\\n\\n${payload.system_prompt}\\n`,
    };
    return roleRecords[payload.role_id];
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
    mock_markdown_path.write_text(
        """
export function parseMarkdown(source = "") {
    return `<article>${String(source)}</article>`;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
        .replace("../../utils/markdown.js", "./mockMarkdown.mjs")
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
    let cachedRoleRecordsSource = "";
    let cachedRoleEditButtons = [];
    let cachedRoleEditButtonsSource = "";
    let cachedInputs = [];
    let cachedInputsSource = "";

    function buildRoleRecords(source) {{
        const matches = [];
        const pattern = /class="role-record([^"]*)" data-role-id="([^"]+)"/g;
        let match = pattern.exec(source);
        while (match) {{
            matches.push({{
                dataset: {{ roleId: match[2] }},
                onclick: null,
                className: `role-record${{match[1]}}`,
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    function buildRoleEditButtons(source) {{
        const matches = [];
        const pattern = /class="[^"]*role-record-edit-btn[^"]*" data-role-id="([^"]+)"/g;
        let match = pattern.exec(source);
        while (match) {{
            matches.push({{
                dataset: {{ roleId: match[1] }},
                onclick: null,
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    function buildCheckboxes(source) {{
        const matches = [];
        const pattern = /<input type="checkbox" data-option-value="([^"]+)"( checked)?>/g;
        let match = pattern.exec(source);
        while (match) {{
            matches.push({{
                dataset: {{ optionValue: match[1] }},
                checked: Boolean(match[2]),
                onchange: null,
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    const element = {{
        style: {{ display: initialDisplay }},
        value: "",
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
                if (cachedRoleRecordsSource !== html) {{
                    cachedRoleRecords = buildRoleRecords(html);
                    cachedRoleRecordsSource = html;
                }}
                return cachedRoleRecords;
            }}
            if (selector === ".role-record-edit-btn") {{
                if (cachedRoleEditButtonsSource !== html) {{
                    cachedRoleEditButtons = buildRoleEditButtons(html);
                    cachedRoleEditButtonsSource = html;
                }}
                return cachedRoleEditButtons;
            }}
            if (selector === 'input[type="checkbox"]') {{
                if (cachedInputsSource !== html) {{
                    cachedInputs = buildCheckboxes(html);
                    cachedInputsSource = html;
                }}
                return cachedInputs;
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
            const selectedOption = html.match(/<option value="([^"]+)" selected>/);
            const firstOption = html.match(/<option value="([^"]+)"/);
            if (selectedOption) {{
                element.value = selectedOption[1];
            }} else if (firstOption) {{
                element.value = firstOption[1];
            }}
            cachedRoleRecordsSource = "";
            cachedRoleEditButtonsSource = "";
            cachedInputsSource = "";
        }},
    }});

    element.classList = createClassList(element);
    return element;
}}

function createElements() {{
    return new Map([
        ["roles-list", createElement("block")],
        ["role-editor-panel", createElement("none")],
        ["roles-editor-empty", createElement("none")],
        ["role-editor-form", createElement("none")],
        ["role-id-input", createElement("block")],
        ["role-name-input", createElement("block")],
        ["role-description-input", createElement("block")],
        ["role-version-input", createElement("block")],
        ["role-model-profile-input", createElement("block")],
        ["role-tools-picker", createElement("block")],
        ["role-mcp-picker", createElement("block")],
        ["role-skills-picker", createElement("block")],
        ["role-memory-enabled-input", createElement("block")],
        ["role-memory-daily-enabled-input", createElement("block")],
        ["role-system-prompt-input", createElement("block")],
        ["role-system-prompt-preview", createElement("none")],
        ["role-file-meta", createElement("block")],
        ["role-editor-status", createElement("none")],
        ["add-role-btn", createElement("block")],
        ["save-role-btn", createElement("block")],
        ["validate-role-btn", createElement("block")],
        ["cancel-role-btn", createElement("block")],
        ["role-prompt-edit-tab", createElement("block")],
        ["role-prompt-preview-tab", createElement("block")],
    ]);
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
    }};
    globalThis.__feedbackNotifications = [];
    globalThis.__fetchRoleConfigsCount = 0;
    globalThis.__fetchRoleConfigCalls = [];
    globalThis.__validatePayload = null;
    globalThis.__saveCalls = [];
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
            "Node runner failed:\\n"
            f"STDOUT:\\n{completed.stdout}\\n"
            f"STDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
