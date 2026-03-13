# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from agent_teams.shared_types.json_types import JsonObject


def test_environment_variables_panel_renders_app_first_and_system_collapsed(
    tmp_path: Path,
) -> None:
    payload = _run_environment_variables_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindEnvironmentVariableSettingsHandlers, loadEnvironmentVariablesPanel } from "./environmentVariables.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindEnvironmentVariableSettingsHandlers();
await loadEnvironmentVariablesPanel();

const groups = document.getElementById("environment-variables-groups");
const toggles = groups.querySelectorAll(".env-scope-toggle");
await toggles[0].onclick();

console.log(JSON.stringify({
    notifications,
    helpText: document.getElementById("env-variables-help").textContent,
    helpDisplay: document.getElementById("env-variables-help").style.display,
    groupsHtml: groups.innerHTML,
    toggleCount: toggles.length,
    addDisplay: document.getElementById("add-env-btn").style.display,
    saveDisplay: document.getElementById("save-env-btn").style.display,
}));
""".strip(),
    )

    assert payload["notifications"] == []
    assert payload["helpText"] == ""
    assert payload["helpDisplay"] == "none"
    groups_html = cast(str, payload["groupsHtml"])
    assert groups_html.index("App Variables") < groups_html.index("System Variables")
    assert "System Variables" in groups_html
    assert "App Variables" in groups_html
    assert "HTTP_PROXY" not in groups_html
    assert "SSL_VERIFY" not in groups_html
    assert payload["toggleCount"] == 1
    assert 'data-env-scope="app"' in groups_html
    assert 'data-env-scope="system"' in groups_html
    assert 'aria-expanded="true"' in groups_html
    assert ">Hide<" in groups_html
    assert ">+<" not in groups_html
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"


def test_environment_variables_add_row_is_inline_and_save_delete_use_app_scope(
    tmp_path: Path,
) -> None:
    payload = _run_environment_variables_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindEnvironmentVariableSettingsHandlers, loadEnvironmentVariablesPanel } from "./environmentVariables.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindEnvironmentVariableSettingsHandlers();
await loadEnvironmentVariablesPanel();
await document.getElementById("add-env-btn").onclick();
const groupsHtmlWithEditor = document.getElementById("environment-variables-groups").innerHTML;
const keyInput = document.getElementById("env-key-input");
document.getElementById("env-key-input").value = "NEW_KEY";
document.getElementById("env-value-input").value = "updated-value";
await document.getElementById("save-env-btn").onclick();

const deleteButtons = document.getElementById("environment-variables-groups").querySelectorAll(".env-delete-btn");
await deleteButtons[0].onclick();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    notifications,
    groupsHtmlWithEditor,
    focusCalls: keyInput.focusCalls,
    scrollCalls: keyInput.scrollCalls,
    savePayload: globalThis.__saveEnvironmentPayload,
    deletePayload: globalThis.__deleteEnvironmentPayload,
    saveCalls: globalThis.__saveEnvironmentCalls,
    deleteCalls: globalThis.__deleteEnvironmentCalls,
    confirmCalls: globalThis.__confirmCalls,
    addDisplay: document.getElementById("add-env-btn").style.display,
    saveDisplay: document.getElementById("save-env-btn").style.display,
}));
""".strip(),
    )

    notifications = cast(list[JsonObject], payload["notifications"])
    groups_html_with_editor = cast(str, payload["groupsHtmlWithEditor"])
    assert 'class="env-record env-record-editor"' in groups_html_with_editor
    assert 'id="env-key-input"' in groups_html_with_editor
    assert 'id="env-value-input"' in groups_html_with_editor
    assert groups_html_with_editor.index(
        "OPENAI_API_KEY"
    ) < groups_html_with_editor.index('id="env-key-input"')
    assert payload["focusCalls"] == 1
    assert payload["scrollCalls"] == 1
    assert payload["savePayload"] == {
        "scope": "app",
        "key": "NEW_KEY",
        "payload": {
            "source_key": None,
            "value": "updated-value",
        },
    }
    assert payload["deletePayload"] == {
        "scope": "app",
        "key": "OPENAI_API_KEY",
    }
    assert payload["saveCalls"] == 1
    assert payload["deleteCalls"] == 1
    assert payload["confirmCalls"] == 1
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"
    assert notifications == [
        {
            "title": "Environment Variable Saved",
            "message": "NEW_KEY saved in app scope.",
            "tone": "success",
        },
        {
            "title": "Environment Variable Deleted",
            "message": "OPENAI_API_KEY removed from app scope.",
            "tone": "success",
        },
    ]


def test_environment_variables_edit_replaces_row_in_place(
    tmp_path: Path,
) -> None:
    payload = _run_environment_variables_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindEnvironmentVariableSettingsHandlers, loadEnvironmentVariablesPanel } from "./environmentVariables.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindEnvironmentVariableSettingsHandlers();
await loadEnvironmentVariablesPanel();

const editButtons = document.getElementById("environment-variables-groups").querySelectorAll(".env-edit-btn");
await editButtons[0].onclick();
const groupsHtmlWithEditor = document.getElementById("environment-variables-groups").innerHTML;
const keyInput = document.getElementById("env-key-input");
document.getElementById("env-key-input").value = "OPENAI_API_KEY";
document.getElementById("env-source-key-input").value = "OPENAI_API_KEY";
document.getElementById("env-value-input").value = "edited-value";
await document.getElementById("save-env-btn").onclick();

console.log(JSON.stringify({
    notifications,
    groupsHtmlWithEditor,
    focusCalls: keyInput.focusCalls,
    scrollCalls: keyInput.scrollCalls,
    savePayload: globalThis.__saveEnvironmentPayload,
}));
""".strip(),
    )

    groups_html_with_editor = cast(str, payload["groupsHtmlWithEditor"])
    assert groups_html_with_editor.count('data-env-key="OPENAI_API_KEY"') == 0
    assert 'id="env-key-input"' in groups_html_with_editor
    assert 'value="OPENAI_API_KEY"' in groups_html_with_editor
    assert payload["focusCalls"] == 1
    assert payload["scrollCalls"] == 1
    assert payload["savePayload"] == {
        "scope": "app",
        "key": "OPENAI_API_KEY",
        "payload": {
            "source_key": "OPENAI_API_KEY",
            "value": "edited-value",
        },
    }


def _run_environment_variables_script(
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
        / "environmentVariables.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "environmentVariables.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchEnvironmentVariables() {
    return {
        system: [
            {
                key: "ComSpec",
                value: "%SystemRoot%\\system32\\cmd.exe",
                scope: "system",
                value_kind: "expandable",
            },
        ],
        app: [
            {
                key: "OPENAI_API_KEY",
                value: "secret",
                scope: "app",
                value_kind: "string",
            },
            {
                key: "HTTP_PROXY",
                value: "http://proxy.example:8080",
                scope: "app",
                value_kind: "string",
            },
            {
                key: "SSL_VERIFY",
                value: "false",
                scope: "app",
                value_kind: "string",
            },
        ],
    };
}

export async function saveEnvironmentVariable(scope, key, payload) {
    globalThis.__saveEnvironmentCalls += 1;
    globalThis.__saveEnvironmentPayload = { scope, key, payload };
    return {
        key,
        value: payload.value,
        scope,
        value_kind: "string",
    };
}

export async function deleteEnvironmentVariable(scope, key) {
    globalThis.__deleteEnvironmentCalls += 1;
    globalThis.__deleteEnvironmentPayload = { scope, key };
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

export async function showConfirmDialog() {
    globalThis.__confirmCalls += 1;
    return true;
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
function createElement(initialDisplay = "block") {{
    const element = {{
        style: {{ display: initialDisplay }},
        value: "",
        textContent: "",
        innerHTML: "",
        onclick: null,
        dataset: {{}},
        focusCalls: 0,
        scrollCalls: 0,
        __selectorCache: new Map(),
        focus() {{
            this.focusCalls += 1;
        }},
        scrollIntoView() {{
            this.scrollCalls += 1;
        }},
        querySelectorAll(selector) {{
            if (!this.__selectorCache.has(selector)) {{
                this.__selectorCache.set(selector, parseSelector(this.innerHTML, selector));
            }}
            return this.__selectorCache.get(selector);
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
    return element;
}}

function parseSelector(html, selector) {{
    const configs = {{
        ".env-scope-toggle": /class="[^"]*env-scope-toggle[^"]*"[^>]*data-env-toggle-scope="([^"]+)"/g,
        ".env-edit-btn": /class="[^"]*env-edit-btn[^"]*"[^>]*data-env-edit="([^"]+)"/g,
        ".env-delete-btn": /class="[^"]*env-delete-btn[^"]*"[^>]*data-env-delete="([^"]+)"/g,
    }};
    const config = configs[selector];
    if (!config) {{
        return [];
    }}
    const matches = [];
    for (const match of html.matchAll(config)) {{
        const element = createElement();
        if (selector === ".env-scope-toggle") {{
            element.dataset.envToggleScope = match[1];
        }} else if (selector === ".env-edit-btn") {{
            element.dataset.envEdit = match[1];
        }} else if (selector === ".env-delete-btn") {{
            element.dataset.envDelete = match[1];
        }}
        matches.push(element);
    }}
    return matches;
}}

function createElements() {{
    return new Map([
        ["add-env-btn", createElement("none")],
        ["save-env-btn", createElement("none")],
        ["cancel-env-btn", createElement("none")],
        ["env-variables-help", createElement()],
        ["env-key-input", createElement()],
        ["env-value-input", createElement()],
        ["env-source-key-input", createElement()],
        ["environment-variables-groups", createElement()],
    ]);
}}

function installGlobals(elements, notifications) {{
    globalThis.__feedbackNotifications = notifications;
    globalThis.__saveEnvironmentPayload = null;
    globalThis.__deleteEnvironmentPayload = null;
    globalThis.__saveEnvironmentCalls = 0;
    globalThis.__deleteEnvironmentCalls = 0;
    globalThis.__confirmCalls = 0;
    globalThis.document = {{
        getElementById(id) {{
            return elements.get(id) || null;
        }},
    }};
}}

{runner_source}
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return cast(dict[str, object], json.loads(completed.stdout))
