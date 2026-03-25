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
export function t(key) {
    return key;
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
