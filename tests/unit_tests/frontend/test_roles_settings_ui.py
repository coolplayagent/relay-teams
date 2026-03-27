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
    boundAgentValue: document.getElementById("role-bound-agent-input").value,
    boundAgentHtml: document.getElementById("role-bound-agent-input").innerHTML,
    memoryEnabled: document.getElementById("role-memory-enabled-input").value,
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
    assert payload["boundAgentValue"] == ""
    assert 'value="" selected>Local runtime</option>' in cast(
        str, payload["boundAgentHtml"]
    )
    assert 'value="codex_local">Codex Local</option>' in cast(
        str, payload["boundAgentHtml"]
    )
    assert payload["memoryEnabled"] == "true"
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
document.getElementById("role-bound-agent-input").value = "codex_local";
document.getElementById("role-description-input").value = "Drafts user-facing content with structure.";
document.getElementById("role-memory-enabled-input").value = "false";
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
    assert validate_payload["bound_agent_id"] == "codex_local"
    assert validate_payload["memory_profile"] == {
        "enabled": False,
    }
    assert validate_payload["model_profile"] == "editor"
    assert payload["firstSavedRoleId"] == "writer"
    assert first_saved_payload == validate_payload
    assert payload["secondSavedRoleId"] == "new_role"
    assert second_saved_payload["source_role_id"] is None
    assert second_saved_payload["role_id"] == "new_role"
    assert second_saved_payload["description"] == "Starts from a blank role."
    assert second_saved_payload["bound_agent_id"] is None
    assert second_saved_payload["tools"] == ["read_file"]
    assert second_saved_payload["memory_profile"] == {
        "enabled": True,
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


def test_role_settings_lists_delete_actions_and_deletes_deletable_role(
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
const deleteButtons = document.getElementById("roles-list").querySelectorAll(".role-record-delete-btn");
await deleteButtons[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    initialListHtml,
    deleteButtonCount: deleteButtons.length,
    deleteCalls: globalThis.__deleteRoleCalls,
    confirmCalls: globalThis.__feedbackConfirms,
    notifications: globalThis.__feedbackNotifications,
    roleSummaryCalls: globalThis.__fetchRoleConfigsCount,
    finalListHtml: document.getElementById("roles-list").innerHTML,
}));
""".strip(),
    )

    confirm_calls = cast(list[dict[str, JsonValue]], payload["confirmCalls"])
    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["deleteButtonCount"] == 2
    assert "role-record-delete-btn" in cast(str, payload["initialListHtml"])
    assert payload["deleteCalls"] == ["writer"]
    assert payload["roleSummaryCalls"] == 2
    assert confirm_calls == [
        {
            "title": "Delete Role",
            "message": "Delete role Writer?",
            "tone": "warning",
            "confirmLabel": "Delete",
            "cancelLabel": "Cancel",
        }
    ]
    assert notifications == [
        {
            "title": "Role Deleted",
            "message": "writer deleted.",
            "tone": "success",
        }
    ]
    assert "Writer" not in cast(str, payload["finalListHtml"])
    assert "Reviewer" in cast(str, payload["finalListHtml"])


def test_role_settings_delete_failure_keeps_list_and_shows_error(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

installGlobals(createElements());
globalThis.__deleteRoleShouldFail = true;
globalThis.__deleteRoleErrorMessage = "Cannot delete role.";
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-delete-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    deleteCalls: globalThis.__deleteRoleCalls,
    notifications: globalThis.__feedbackNotifications,
    roleSummaryCalls: globalThis.__fetchRoleConfigsCount,
    listHtml: document.getElementById("roles-list").innerHTML,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["deleteCalls"] == ["writer"]
    assert payload["roleSummaryCalls"] == 1
    assert notifications == [
        {
            "title": "Delete Failed",
            "message": "Cannot delete role.",
            "tone": "danger",
        }
    ]
    assert "Writer" in cast(str, payload["listHtml"])


def test_role_settings_shows_shell_advisory_when_skills_are_selected(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn")[1].onclick({ stopPropagation() {} });
const advisoryBefore = document.getElementById("role-skills-picker").innerHTML;
const toolOptions = document.getElementById("role-tools-picker").querySelectorAll('input[type="checkbox"]');
toolOptions[2].checked = true;
toolOptions[2].onchange();
const advisoryAfter = document.getElementById("role-skills-picker").innerHTML;

console.log(JSON.stringify({
    advisoryBefore,
    advisoryAfter,
}));
""".strip(),
    )

    assert (
        "Roles that use skills usually work better with the shell tool enabled."
        in cast(str, payload["advisoryBefore"])
    )
    assert (
        "Roles that use skills usually work better with the shell tool enabled."
        not in cast(str, payload["advisoryAfter"])
    )


def test_role_settings_renders_skill_option_objects_by_name(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

globalThis.__roleConfigOptionsOverride = {
    skills: [
        { ref: "builtin:diff", name: "diff", description: "Inspect file changes before replying.", scope: "builtin" },
        { ref: "app:time", name: "time", description: "Read the current wall-clock time.", scope: "app" },
    ],
};

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn")[1].onclick({ stopPropagation() {} });
const skillOptions = document.getElementById("role-skills-picker").querySelectorAll('input[type="checkbox"]');

console.log(JSON.stringify({
    skillsHtml: document.getElementById("role-skills-picker").innerHTML,
    skillValues: skillOptions.map(input => input.dataset.optionValue),
}));
""".strip(),
    )

    assert "[object Object]" not in cast(str, payload["skillsHtml"])
    assert "diff" in cast(str, payload["skillsHtml"])
    assert "time" in cast(str, payload["skillsHtml"])
    assert "BUILTIN" not in cast(str, payload["skillsHtml"])
    assert "APP" not in cast(str, payload["skillsHtml"])
    assert payload["skillValues"] == ["builtin:diff", "app:time"]


def test_role_settings_disambiguates_only_duplicate_skill_names(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

globalThis.__roleConfigOptionsOverride = {
    skills: [
        { ref: "builtin:diff", name: "diff", description: "Inspect file changes before replying.", scope: "builtin" },
        { ref: "builtin:time", name: "time", description: "Read builtin time.", scope: "builtin" },
        { ref: "app:time", name: "time", description: "Read app time.", scope: "app" },
    ],
};

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn")[1].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    skillsHtml: document.getElementById("role-skills-picker").innerHTML,
}));
""".strip(),
    )

    skills_html = cast(str, payload["skillsHtml"])
    assert "diff" in skills_html
    assert "diff" in skills_html and "BUILTIN" not in skills_html.split("diff", 1)[1][:20]
    assert "time" in skills_html
    assert "BUILTIN" in skills_html
    assert "APP" in skills_html


def test_role_settings_render_default_alias_with_current_profile_name(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

globalThis.__modelProfilesOverride = {
    moonshot: { model: "kimi-k2.5", is_default: true },
    default: { model: "legacy-default" },
};

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    modelProfileHtml: document.getElementById("role-model-profile-input").innerHTML,
}));
""".strip(),
    )

    model_profile_html = cast(str, payload["modelProfileHtml"])
    assert (
        'value="default" selected>default (current: moonshot)</option>'
        in model_profile_html
    )
    assert 'value="moonshot">moonshot</option>' in model_profile_html


def test_role_settings_marks_main_agent_and_keeps_reserved_prompt_editable(
    tmp_path: Path,
) -> None:
    payload = _run_roles_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from "./rolesSettings.mjs";

installGlobals(createElements());
bindRoleSettingsHandlers();
await loadRoleSettingsPanel();

await document.getElementById("roles-list").querySelectorAll(".role-record-edit-btn")[2].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    listHtml: document.getElementById("roles-list").innerHTML,
    promptReadonly: document.getElementById("role-system-prompt-input").readOnly,
    promptTitle: document.getElementById("role-system-prompt-input").title,
    statusText: document.getElementById("role-editor-status").textContent,
}));
""".strip(),
    )

    assert "Main Agent only" in cast(str, payload["listHtml"])
    assert "Normal Mode" in cast(str, payload["listHtml"])
    assert payload["promptReadonly"] is False
    assert "normal mode" in cast(str, payload["promptTitle"])
    assert "only used in normal mode" in cast(str, payload["statusText"])


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
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_markdown_path = tmp_path / "mockMarkdown.mjs"
    module_under_test_path = tmp_path / "rolesSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
const defaultRoleRecords = {
    writer: {
        source_role_id: "writer",
        role_id: "writer",
        name: "Writer",
        description: "Drafts user-facing content.",
        version: "1.0.0",
        bound_agent_id: "codex_local",
        tools: ["read_file"],
        mcp_servers: [],
        skills: [],
        model_profile: "default",
        memory_profile: { enabled: true },
        system_prompt: "Write the first draft.",
        file_name: "writer.md",
        content: "---\\nrole_id: writer\\n---\\n\\nWrite the first draft.\\n",
        deletable: true,
    },
    reviewer: {
        source_role_id: "reviewer",
        role_id: "reviewer",
        name: "Reviewer",
        description: "Reviews delivered work.",
        version: "1.0.0",
        bound_agent_id: null,
        tools: ["read_file", "write_file"],
        mcp_servers: ["docs"],
        skills: ["builtin:diff"],
        model_profile: "default",
        memory_profile: { enabled: true },
        system_prompt: "Review the delivered work.",
        file_name: "reviewer.md",
        content: "---\\nrole_id: reviewer\\n---\\n\\nReview the delivered work.\\n",
        deletable: true,
    },
    MainAgent: {
        source_role_id: "MainAgent",
        role_id: "MainAgent",
        name: "Main Agent",
        description: "Handles normal-mode runs directly.",
        version: "1.0.0",
        bound_agent_id: null,
        tools: ["read_file"],
        mcp_servers: [],
        skills: [],
        model_profile: "default",
        memory_profile: { enabled: true },
        system_prompt: "Handle the run directly.",
        file_name: "main_agent.md",
        content: "---\\nrole_id: MainAgent\\n---\\n\\nHandle the run directly.\\n",
        deletable: false,
    },
    Coordinator: {
        source_role_id: "Coordinator",
        role_id: "Coordinator",
        name: "Coordinator",
        description: "Coordinates delegated work.",
        version: "1.0.0",
        bound_agent_id: null,
        tools: ["create_tasks", "dispatch_task"],
        mcp_servers: [],
        skills: [],
        model_profile: "default",
        memory_profile: { enabled: true },
        system_prompt: "Coordinate the run.",
        file_name: "coordinator.md",
        content: "---\\nrole_id: Coordinator\\n---\\n\\nCoordinate the run.\\n",
        deletable: false,
    },
};

function getRoleRecords() {
    return globalThis.__roleRecordsOverride || defaultRoleRecords;
}

export async function fetchRoleConfigs() {
    globalThis.__fetchRoleConfigsCount += 1;
    return Object.values(getRoleRecords()).map(record => ({
        role_id: record.role_id,
        name: record.name,
        description: record.description,
        version: record.version,
        bound_agent_id: record.bound_agent_id,
        model_profile: record.model_profile,
        deletable: record.deletable === true,
    }));
}

export async function fetchRoleConfigOptions() {
    const defaults = {
        coordinator_role_id: "Coordinator",
        main_agent_role_id: "MainAgent",
        tools: ["read_file", "write_file", "shell"],
        mcp_servers: ["docs"],
        skills: [
            { ref: "builtin:diff", name: "diff", description: "Inspect file changes before replying.", scope: "builtin" },
            { ref: "builtin:time", name: "time", description: "Read the current wall-clock time.", scope: "builtin" },
        ],
        agents: [
            { agent_id: "codex_local", name: "Codex Local", transport: "stdio" },
            { agent_id: "claude_http", name: "Claude HTTP", transport: "streamable_http" },
        ],
    };
    return {
        ...defaults,
        ...(globalThis.__roleConfigOptionsOverride || {}),
    };
}

export async function fetchModelProfiles() {
    return globalThis.__modelProfilesOverride || {
        default: { model: "gpt-4o-mini" },
        editor: { model: "gpt-4.1" },
    };
}

export async function fetchRoleConfig(roleId) {
    globalThis.__fetchRoleConfigCalls.push(roleId);
    return getRoleRecords()[roleId];
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
    const roleRecords = getRoleRecords();
    roleRecords[payload.role_id] = {
        ...payload,
        source_role_id: payload.source_role_id,
        file_name: `${payload.role_id}.md`,
        content: `---\\nrole_id: ${payload.role_id}\\n---\\n\\n${payload.system_prompt}\\n`,
    };
    return roleRecords[payload.role_id];
}

export async function deleteRoleConfig(roleId) {
    globalThis.__deleteRoleCalls.push(roleId);
    if (globalThis.__deleteRoleShouldFail) {
        throw new Error(globalThis.__deleteRoleErrorMessage || "Delete failed.");
    }
    delete getRoleRecords()[roleId];
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

export async function showConfirmDialog(payload) {
    globalThis.__feedbackConfirms.push(payload);
    return globalThis.__confirmResult !== false;
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "composer.mode_normal": "Normal Mode",
    "settings.tab.orchestration": "Orchestration",
    "settings.action.delete": "Delete",
    "settings.action.cancel": "Cancel",
    "settings.roles.edit": "Edit",
    "settings.roles.delete_confirm_title": "Delete Role",
    "settings.roles.delete_confirm_message": "Delete role {name}?",
    "settings.roles.deleted": "Role Deleted",
    "settings.roles.deleted_message": "{role_id} deleted.",
    "settings.roles.delete_failed": "Delete Failed",
    "settings.roles.delete_failed_message": "Failed to delete role config.",
    "settings.roles.disabled": "Disabled",
    "settings.roles.none": "No roles found",
    "settings.roles.none_copy": "Add a role to edit its metadata and prompt.",
    "settings.roles.file_label": "File: {file}",
    "settings.roles.new_role": "New role",
    "settings.roles.validated": "Role Validated",
    "settings.roles.validated_message": "Validated successfully.",
    "settings.roles.validated_toast": "{role_id} passed validation.",
    "settings.roles.validation_failed": "Validation Failed",
    "settings.roles.validation_failed_message": "Validation failed.",
    "settings.roles.validation_failed_toast": "Failed to validate role config.",
    "settings.roles.saved": "Role Saved",
    "settings.roles.saved_message": "Saved and validated.",
    "settings.roles.saved_toast": "{role_id} saved and reloaded.",
    "settings.roles.save_failed": "Save Failed",
    "settings.roles.save_failed_message": "Save failed.",
    "settings.roles.save_failed_toast": "Failed to save role config.",
    "settings.roles.default_current": "default (current: {profile})",
    "settings.roles.main_agent_only": "Main Agent only",
    "settings.roles.coordinator_root": "Coordinator root",
    "settings.roles.main_agent_fixed": "Main Agent keeps a fixed identity. Its base prompt is edited here and is only used in normal mode.",
    "settings.roles.coordinator_fixed": "Coordinator keeps a fixed identity. Its base prompt is edited here and is combined with the selected preset orchestration prompt in Orchestrated Mode.",
    "settings.roles.main_agent_title": "Main Agent base prompt is edited here and used only in normal mode.",
    "settings.roles.coordinator_title": "Coordinator base prompt is edited here and combined with the selected preset orchestration prompt in Orchestrated Mode.",
    "settings.roles.no_tools": "No tools loaded.",
    "settings.roles.no_mcp": "No MCP servers loaded.",
    "settings.roles.no_skills": "No skills loaded.",
    "settings.roles.skills_shell_advisory": "Roles that use skills usually work better with the shell tool enabled.",
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
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
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
    let cachedRoleDeleteButtons = [];
    let cachedRoleDeleteButtonsSource = "";
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

    function buildRoleDeleteButtons(source) {{
        const matches = [];
        const pattern = /class="[^"]*role-record-delete-btn[^"]*" data-role-id="([^"]+)"/g;
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
            if (selector === ".role-record-delete-btn") {{
                if (cachedRoleDeleteButtonsSource !== html) {{
                    cachedRoleDeleteButtons = buildRoleDeleteButtons(html);
                    cachedRoleDeleteButtonsSource = html;
                }}
                return cachedRoleDeleteButtons;
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
            const selectedOption = html.match(/<option value="([^"]*)" selected>/);
            const firstOption = html.match(/<option value="([^"]*)"/);
            if (selectedOption) {{
                element.value = selectedOption[1];
            }} else if (firstOption) {{
                element.value = firstOption[1];
            }}
            cachedRoleRecordsSource = "";
            cachedRoleEditButtonsSource = "";
            cachedRoleDeleteButtonsSource = "";
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
        ["role-bound-agent-input", createElement("block")],
        ["role-tools-picker", createElement("block")],
        ["role-mcp-picker", createElement("block")],
        ["role-skills-picker", createElement("block")],
        ["role-memory-enabled-input", createElement("block")],
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
    const previousRoleConfigOptionsOverride = globalThis.__roleConfigOptionsOverride ?? null;
    const previousModelProfilesOverride = globalThis.__modelProfilesOverride ?? null;
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
    globalThis.__feedbackConfirms = [];
    globalThis.__fetchRoleConfigsCount = 0;
    globalThis.__fetchRoleConfigCalls = [];
    globalThis.__validatePayload = null;
    globalThis.__saveCalls = [];
    globalThis.__deleteRoleCalls = [];
    globalThis.__deleteRoleShouldFail = false;
    globalThis.__deleteRoleErrorMessage = "";
    globalThis.__confirmResult = true;
    globalThis.__roleConfigOptionsOverride = previousRoleConfigOptionsOverride;
    globalThis.__modelProfilesOverride = previousModelProfilesOverride;
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
