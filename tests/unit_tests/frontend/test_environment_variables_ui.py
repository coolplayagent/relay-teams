# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from agent_teams.shared_types.json_types import JsonObject


def test_environment_variables_panel_loads_scoped_groups_and_supports_collapse(
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
    groupsHtml: groups.innerHTML,
    addDisplay: document.getElementById("add-env-btn").style.display,
    saveDisplay: document.getElementById("save-env-btn").style.display,
}));
""".strip(),
    )

    assert payload["notifications"] == []
    assert "Windows registry" in cast(str, payload["helpText"])
    groups_html = cast(str, payload["groupsHtml"])
    assert "System Variables" in groups_html
    assert "User Variables" in groups_html
    assert 'style="display:none;"' in groups_html
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"


def test_environment_variables_save_and_delete_use_current_form_values(
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
document.getElementById("env-scope-select").value = "user";
document.getElementById("env-key-input").value = "NEW_KEY";
document.getElementById("env-value-input").value = "updated-value";
await document.getElementById("save-env-btn").onclick();

const deleteButtons = document.getElementById("environment-variables-groups").querySelectorAll(".env-delete-btn");
await deleteButtons[0].onclick();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    notifications,
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
    assert payload["savePayload"] == {
        "scope": "user",
        "key": "NEW_KEY",
        "payload": {
            "source_key": None,
            "value": "updated-value",
        },
    }
    assert payload["deletePayload"] == {
        "scope": "system",
        "key": "ComSpec",
    }
    assert payload["saveCalls"] == 1
    assert payload["deleteCalls"] == 1
    assert payload["confirmCalls"] == 1
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"
    assert notifications == [
        {
            "title": "Environment Variable Saved",
            "message": "NEW_KEY saved in user scope.",
            "tone": "success",
        },
        {
            "title": "Environment Variable Deleted",
            "message": "ComSpec removed from system scope.",
            "tone": "success",
        },
    ]


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
        user: [
            {
                key: "OPENAI_API_KEY",
                value: "secret",
                scope: "user",
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
        __selectorCache: new Map(),
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
        ["env-editor-shell", createElement("none")],
        ["env-scope-select", createElement()],
        ["env-key-input", createElement()],
        ["env-value-input", createElement()],
        ["env-source-key-input", createElement()],
        ["env-editor-title", createElement()],
        ["env-editor-meta", createElement()],
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
