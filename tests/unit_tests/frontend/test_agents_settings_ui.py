# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast


def test_agents_settings_loads_preferred_agent_and_saves_updates(
    tmp_path: Path,
) -> None:
    payload = _run_agents_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindAgentSettingsHandlers, loadAgentSettingsPanel } from "./agentsSettings.mjs";

installGlobals(createElements());
bindAgentSettingsHandlers();
await loadAgentSettingsPanel("codex_local");

document.getElementById("agent-name-input").value = "Codex Local Updated";
document.getElementById("agent-stdio-command-input").value = "codex --serve";

await document.getElementById("save-agent-btn").onclick();
await document.getElementById("test-agent-btn").onclick();

console.log(JSON.stringify({
    selectedAgentId: document.getElementById("agent-id-input").value,
    transportValue: document.getElementById("agent-transport-input").value,
    commandValue: document.getElementById("agent-stdio-command-input").value,
    saveCalls: globalThis.__saveCalls,
    testCalls: globalThis.__testCalls,
    toasts: globalThis.__toasts,
    statusText: document.getElementById("agent-editor-status").textContent,
}));
""".strip(),
    )

    save_calls = cast(list[dict[str, JsonValue]], payload["saveCalls"])
    test_calls = cast(list[str], payload["testCalls"])
    toasts = cast(list[dict[str, JsonValue]], payload["toasts"])
    assert payload["selectedAgentId"] == "codex_local"
    assert payload["transportValue"] == "stdio"
    assert payload["commandValue"] == "codex --serve"
    assert save_calls[0]["agentId"] == "codex_local"
    assert cast(dict[str, JsonValue], save_calls[0]["payload"]) == {
        "agent_id": "codex_local",
        "name": "Codex Local Updated",
        "description": "Runs Codex locally.",
        "transport": {
            "transport": "stdio",
            "command": "codex --serve",
            "args": ["--serve"],
            "env": [],
        },
    }
    assert test_calls == ["codex_local"]
    assert toasts[0]["title"] == "Agent Saved"
    assert toasts[1]["title"] == "Agent Test Passed"
    assert payload["statusText"] == "Connected"


def test_agents_settings_delete_uses_selected_agent_id(tmp_path: Path) -> None:
    payload = _run_agents_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindAgentSettingsHandlers, loadAgentSettingsPanel } from "./agentsSettings.mjs";

installGlobals(createElements());
bindAgentSettingsHandlers();
await loadAgentSettingsPanel("codex_local");
await document.getElementById("delete-agent-btn").onclick();

console.log(JSON.stringify({
    deleteCalls: globalThis.__deleteCalls,
    toasts: globalThis.__toasts,
}));
""".strip(),
    )

    assert payload["deleteCalls"] == ["codex_local"]
    toasts = cast(list[dict[str, JsonValue]], payload["toasts"])
    assert toasts[0]["title"] == "Agent Deleted"


def test_agents_settings_stdio_environment_bindings_use_settings_variables(
    tmp_path: Path,
) -> None:
    payload = _run_agents_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindAgentSettingsHandlers, loadAgentSettingsPanel } from "./agentsSettings.mjs";

installGlobals(createElements());
bindAgentSettingsHandlers();
await loadAgentSettingsPanel("codex_local");
await document.getElementById("add-agent-stdio-env-btn").onclick();
await document.getElementById("save-agent-btn").onclick();

console.log(JSON.stringify({
    envListHtml: document.getElementById("agent-stdio-env-list").innerHTML,
    saveCalls: globalThis.__saveCalls,
}));
""".strip(),
    )

    save_calls = cast(list[dict[str, JsonValue]], payload["saveCalls"])
    env_list_html = cast(str, payload["envListHtml"])
    assert "agent-binding-name-select" in env_list_html
    assert "OPENAI_API_KEY" in env_list_html
    assert "App variable" in env_list_html
    assert "agent-binding-value" not in env_list_html
    assert cast(dict[str, JsonValue], save_calls[0]["payload"]) == {
        "agent_id": "codex_local",
        "name": "Codex Local",
        "description": "Runs Codex locally.",
        "transport": {
            "transport": "stdio",
            "command": "codex",
            "args": ["--serve"],
            "env": [
                {
                    "name": "OPENAI_API_KEY",
                    "value": "sk-live",
                    "secret": False,
                    "configured": False,
                }
            ],
        },
    }


def test_agents_settings_panel_markup_uses_i18n_keys() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    panel_start = source_text.index('<div class="settings-panel" id="agents-panel"')
    panel_end = source_text.index(
        '<div class="settings-panel" id="roles-panel"', panel_start
    )
    panel_html = source_text[panel_start:panel_end]

    assert 'data-i18n="settings.agents.empty"' in panel_html
    assert 'data-i18n="settings.agents.editor"' in panel_html
    assert 'data-i18n="settings.agents.env_bindings"' in panel_html
    assert 'data-i18n="settings.agents.header_bindings"' in panel_html
    assert 'data-i18n-placeholder="settings.agents.id_placeholder"' in panel_html
    assert 'data-i18n-placeholder="settings.agents.command_placeholder"' in panel_html
    assert 'data-i18n="settings.agents.transport_http"' in panel_html
    assert 'data-i18n="settings.action.add_agent"' in source_text
    assert 'data-i18n="settings.action.delete"' in source_text


def _run_agents_settings_script(
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
        / "agentsSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "agentsSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
const agentRecords = {
    codex_local: {
        agent_id: "codex_local",
        name: "Codex Local",
        description: "Runs Codex locally.",
        transport: {
            transport: "stdio",
            command: "codex",
            args: ["--serve"],
            env: [],
        },
    },
};

export async function fetchExternalAgents() {
    return [
        {
            agent_id: "codex_local",
            name: "Codex Local",
            description: "Runs Codex locally.",
            transport: "stdio",
        },
    ];
}

export async function fetchExternalAgent(agentId) {
    return agentRecords[agentId];
}

export async function fetchEnvironmentVariables() {
    return {
        app: [
            {
                key: "OPENAI_API_KEY",
                value: "sk-live",
                scope: "app",
                value_kind: "string",
            },
            {
                key: "HTTP_PROXY",
                value: "http://hidden.proxy",
                scope: "app",
                value_kind: "string",
            },
        ],
        system: [
            {
                key: "PATH",
                value: "/usr/bin",
                scope: "system",
                value_kind: "string",
            },
        ],
    };
}

export async function saveExternalAgent(agentId, payload) {
    globalThis.__saveCalls.push({ agentId, payload });
    agentRecords[payload.agent_id] = payload;
    return payload;
}

export async function testExternalAgent(agentId) {
    globalThis.__testCalls.push(agentId);
    return {
        ok: true,
        message: "Connected",
    };
}

export async function deleteExternalAgent(agentId) {
    globalThis.__deleteCalls.push(agentId);
    delete agentRecords[agentId];
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export function showToast(payload) {
    globalThis.__toasts.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const TRANSLATIONS = {
    "settings.roles.edit": "Edit",
    "settings.agents.saved": "Agent Saved",
    "settings.agents.saved_message": "saved and reloaded.",
    "settings.agents.saved_status": "Saved successfully.",
    "settings.agents.save_failed": "Save Failed",
    "settings.agents.save_failed_message": "Failed to save external agent config.",
    "settings.agents.test_passed": "Agent Test Passed",
    "settings.agents.test_passed_message": "Connection succeeded.",
    "settings.agents.test_passed_detail": "responded to ACP initialize.",
    "settings.agents.test_failed": "Agent Test Failed",
    "settings.agents.test_failed_message": "Failed to test external agent config.",
    "settings.agents.deleted": "Agent Deleted",
    "settings.agents.deleted_message": "removed from settings.",
    "settings.agents.delete_failed": "Delete Failed",
    "settings.agents.delete_failed_message": "Failed to delete external agent config.",
    "settings.agents.select_to_delete": "Select an agent to delete.",
    "settings.agents.id_required": "Agent ID is required.",
    "settings.agents.name_required": "Agent name is required.",
    "settings.agents.http_url_required": "HTTP transport URL is required.",
    "settings.agents.custom_adapter_required": "Custom transport adapter ID is required.",
    "settings.agents.stdio_command_required": "Stdio command is required.",
    "settings.agents.custom_config": "Config JSON",
    "settings.agents.json_object_required": "must be a JSON object.",
    "settings.agents.json_invalid": "must be valid JSON.",
    "settings.agents.transport_stdio_label": "Stdio",
    "settings.agents.transport_http_label": "HTTP",
    "settings.agents.transport_custom_label": "Custom",
    "settings.agents.no_description": "No description",
    "settings.agents.none": "No external agents found",
    "settings.agents.none_copy": "Add an ACP-compatible external agent to make it available for role bindings.",
    "settings.agents.load_failed": "Load Failed",
    "settings.agents.load_failed_message": "Unable to load agent settings.",
    "settings.agents.no_env_options": "No environment variables available",
    "settings.agents.no_env_options_copy": "Add environment variables in Settings > Environment first.",
    "settings.agents.no_env_bindings": "No environment variables selected.",
    "settings.agents.no_headers": "No headers configured.",
    "settings.agents.select_env": "Select environment variable",
    "settings.agents.action_label": "Action",
    "settings.agents.action_remove": "Remove",
    "settings.agents.header_name": "Header",
    "settings.agents.header_value": "Value",
    "settings.agents.secret_mode": "Secret",
    "settings.agents.secret_plain": "Plain",
    "settings.agents.secret_keyring": "Keyring",
    "settings.agents.secret_configured": "Configured in keyring",
    "settings.agents.env_missing": "missing",
    "settings.agents.env_missing_note": "Missing from Settings > Environment.",
    "settings.agents.env_scope_app": "App variable",
    "settings.agents.env_scope_system": "System variable",
    "settings.agents.env_value_kind_secret": "Secret",
    "settings.agents.env_value_kind_masked": "Masked",
    "settings.agents.env_value_kind_string": "String",
};

export function t(key) {
    return TRANSLATIONS[key] || key;
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
    const element = {{
        style: {{ display: initialDisplay }},
        value: "",
        textContent: "",
        className: "",
        dataset: {{}},
        onclick: null,
        oninput: null,
        onchange: null,
        focus() {{
            return undefined;
        }},
        querySelectorAll() {{
            return [];
        }},
    }};

    Object.defineProperty(element, "innerHTML", {{
        get() {{
            return html;
        }},
        set(value) {{
            html = String(value || "");
            const selectedOption = html.match(/<option value="([^"]+)" selected>/);
            const firstOption = html.match(/<option value="([^"]+)"/);
            if (selectedOption) {{
                element.value = selectedOption[1];
            }} else if (firstOption) {{
                element.value = firstOption[1];
            }}
        }},
    }});

    element.classList = createClassList(element);
    return element;
}}

function createElements() {{
    return new Map([
        ["agents-list", createElement("block")],
        ["agent-editor-panel", createElement("none")],
        ["agents-editor-empty", createElement("none")],
        ["agent-editor-form", createElement("none")],
        ["agent-id-input", createElement("block")],
        ["agent-name-input", createElement("block")],
        ["agent-description-input", createElement("block")],
        ["agent-transport-input", createElement("block")],
        ["agent-transport-stdio", createElement("block")],
        ["agent-transport-http", createElement("none")],
        ["agent-transport-custom", createElement("none")],
        ["agent-stdio-command-input", createElement("block")],
        ["agent-stdio-args-input", createElement("block")],
        ["agent-stdio-env-list", createElement("block")],
        ["agent-http-url-input", createElement("block")],
        ["agent-http-ssl-verify-input", createElement("block")],
        ["agent-http-header-list", createElement("block")],
        ["agent-custom-adapter-id-input", createElement("block")],
        ["agent-custom-config-input", createElement("block")],
        ["agent-editor-status", createElement("none")],
        ["add-agent-btn", createElement("block")],
        ["save-agent-btn", createElement("block")],
        ["test-agent-btn", createElement("block")],
        ["delete-agent-btn", createElement("block")],
        ["cancel-agent-btn", createElement("block")],
        ["add-agent-stdio-env-btn", createElement("block")],
        ["add-agent-http-header-btn", createElement("block")],
    ]);
}}

function installGlobals(elements) {{
    globalThis.document = {{
        addEventListener() {{
            return undefined;
        }},
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
    }};
    globalThis.__saveCalls = [];
    globalThis.__testCalls = [];
    globalThis.__deleteCalls = [];
    globalThis.__toasts = [];
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
