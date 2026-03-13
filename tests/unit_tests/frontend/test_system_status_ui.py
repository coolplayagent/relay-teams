# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast

DEFAULT_MOCK_API_SOURCE = """
const initialStatus = {
    mcp: {
        servers: ['time-mcp', 'empty-mcp', 'broken-mcp'],
    },
    skills: {
        skills: ['diff'],
    },
};

const reloadedStatus = {
    mcp: {
        servers: ['time-mcp'],
    },
    skills: {
        skills: ['diff'],
    },
};

const initialToolSummaries = {
    'time-mcp': {
        source: 'project',
        transport: 'stdio',
        tools: [
            { name: 'current_time', description: 'Return the current time.' },
            { name: 'format_timezone', description: '' },
        ],
    },
    'empty-mcp': {
        source: 'user',
        transport: 'http',
        tools: [],
    },
};

const reloadedToolSummaries = {
    'time-mcp': {
        source: 'project',
        transport: 'stdio',
        tools: [
            { name: 'format_time_range', description: 'Format a time range.' },
        ],
    },
};

export async function fetchConfigStatus() {
    globalThis.__fetchConfigStatusCalls += 1;
    return globalThis.__reloadMcpCalls > 0 ? reloadedStatus : initialStatus;
}

export async function fetchMcpServerTools(serverName) {
    globalThis.__toolFetchCalls.push(serverName);
    const toolSummaries = globalThis.__reloadMcpCalls > 0
        ? reloadedToolSummaries
        : initialToolSummaries;
    if (serverName === 'broken-mcp') {
        throw new Error('Connection closed');
    }
    return toolSummaries[serverName];
}

export async function reloadMcpConfig() {
    globalThis.__reloadMcpCalls += 1;
    return { status: 'ok' };
}

export async function reloadSkillsConfig() {
    globalThis.__reloadSkillsCalls += 1;
    return { status: 'ok' };
}
""".strip()


def test_mcp_status_panel_lists_loaded_tools_and_server_level_fallbacks(
    tmp_path: Path,
) -> None:
    payload = _run_system_status_script(
        tmp_path=tmp_path,
        runner_source="""
const { bindSystemStatusHandlers, loadMcpStatusPanel } = await import('./systemStatus.mjs');

installGlobals(createElements());
bindSystemStatusHandlers();
await loadMcpStatusPanel();

globalThis.__agentTeamsToggleMcpTools('time-mcp');
const collapsedHtml = document.getElementById('mcp-status').innerHTML;
globalThis.__agentTeamsToggleAllMcpTools();
const expandedAgainHtml = document.getElementById('mcp-status').innerHTML;

console.log(JSON.stringify({
    html: expandedAgainHtml,
    collapsedHtml,
    toolFetchCalls: globalThis.__toolFetchCalls,
    logEntries: globalThis.__logEntries,
}));
""".strip(),
    )

    html = cast(str, payload["html"])
    collapsed_html = cast(str, payload["collapsedHtml"])
    log_entries = cast(list[JsonValue], payload["logEntries"])
    assert "Collapse all tools" in html
    assert "Collapse tools" in html
    assert "time-mcp" in html
    assert "stdio / project" in html
    assert "current_time" in html
    assert "Return the current time." in html
    assert "format_timezone" in html
    assert "No description provided." in html
    assert "empty-mcp" in html
    assert "No tools exposed by this MCP server." in html
    assert "broken-mcp" in html
    assert "Connection closed" in html
    assert "Expand all tools" in collapsed_html
    assert "Expand tools" in collapsed_html
    assert "2 tools hidden." in collapsed_html
    assert "current_time" not in collapsed_html
    assert payload["toolFetchCalls"] == ["time-mcp", "empty-mcp", "broken-mcp"]
    assert log_entries == [
        {
            "eventName": "frontend.system_status.mcp_tools_load_failed",
            "message": "Failed to load MCP tools",
            "payload": {
                "error_message": "Connection closed",
                "server_name": "broken-mcp",
            },
        }
    ]


def test_mcp_status_panel_shows_loading_shell_before_tools_finish(
    tmp_path: Path,
) -> None:
    payload = _run_system_status_script(
        tmp_path=tmp_path,
        mock_api_source="""
const status = {
    mcp: {
        servers: ['slow-mcp'],
    },
    skills: {
        skills: [],
    },
};

let resolveSlowTools;
const slowToolsPromise = new Promise(resolve => {
    resolveSlowTools = resolve;
});

export async function fetchConfigStatus() {
    globalThis.__fetchConfigStatusCalls += 1;
    return status;
}

export async function fetchMcpServerTools(serverName) {
    globalThis.__toolFetchCalls.push(serverName);
    globalThis.__resolveSlowTools = resolveSlowTools;
    return slowToolsPromise;
}

export async function reloadMcpConfig() {
    globalThis.__reloadMcpCalls += 1;
    return { status: 'ok' };
}

export async function reloadSkillsConfig() {
    globalThis.__reloadSkillsCalls += 1;
    return { status: 'ok' };
}
""".strip(),
        runner_source="""
const { bindSystemStatusHandlers, loadMcpStatusPanel } = await import('./systemStatus.mjs');

installGlobals(createElements());
bindSystemStatusHandlers();
const loadPromise = loadMcpStatusPanel();
await Promise.resolve();
const loadingHtml = document.getElementById('mcp-status').innerHTML;

globalThis.__resolveSlowTools({
    source: 'project',
    transport: 'stdio',
    tools: [
        { name: 'slow_tool', description: 'Eventually available.' },
    ],
});
await loadPromise;

console.log(JSON.stringify({
    loadingHtml,
    finalHtml: document.getElementById('mcp-status').innerHTML,
    toolFetchCalls: globalThis.__toolFetchCalls,
}));
""".strip(),
    )

    loading_html = cast(str, payload["loadingHtml"])
    final_html = cast(str, payload["finalHtml"])
    assert "slow-mcp" in loading_html
    assert "Loading.." in loading_html
    assert "Loading tools..." in loading_html
    assert "slow_tool" not in loading_html
    assert payload["toolFetchCalls"] == ["slow-mcp"]
    assert "slow_tool" in final_html
    assert "Eventually available." in final_html
    assert "Collapse tools" in final_html


def test_reload_mcp_button_reloads_config_and_refreshes_tool_list(
    tmp_path: Path,
) -> None:
    payload = _run_system_status_script(
        tmp_path=tmp_path,
        runner_source="""
const { bindSystemStatusHandlers, loadMcpStatusPanel } = await import('./systemStatus.mjs');

installGlobals(createElements());
bindSystemStatusHandlers();
await loadMcpStatusPanel();
await document.getElementById('reload-mcp-btn').onclick();

globalThis.__agentTeamsToggleAllMcpTools();
const collapsedHtml = document.getElementById('mcp-status').innerHTML;
globalThis.__agentTeamsToggleAllMcpTools();
const expandedHtml = document.getElementById('mcp-status').innerHTML;

console.log(JSON.stringify({
    html: expandedHtml,
    collapsedHtml,
    fetchConfigStatusCalls: globalThis.__fetchConfigStatusCalls,
    reloadMcpCalls: globalThis.__reloadMcpCalls,
    toolFetchCalls: globalThis.__toolFetchCalls,
    toasts: globalThis.__toasts,
}));
""".strip(),
    )

    html = cast(str, payload["html"])
    collapsed_html = cast(str, payload["collapsedHtml"])
    toasts = cast(list[JsonValue], payload["toasts"])
    assert payload["fetchConfigStatusCalls"] == 2
    assert payload["reloadMcpCalls"] == 1
    assert payload["toolFetchCalls"] == [
        "time-mcp",
        "empty-mcp",
        "broken-mcp",
        "time-mcp",
    ]
    assert "Collapse all tools" in html
    assert "Collapse tools" in html
    assert "current_time" not in html
    assert "format_time_range" in html
    assert "Expand all tools" in collapsed_html
    assert "1 tool hidden." in collapsed_html
    assert "format_time_range" not in collapsed_html
    assert toasts == [
        {
            "title": "MCP Reloaded",
            "message": "MCP config reloaded.",
            "tone": "success",
        }
    ]


def test_system_status_styles_include_mcp_tool_list_tokens() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert ".mcp-status-shell {" in components_css
    assert ".mcp-status-toolbar {" in components_css
    assert ".mcp-status-toolbar-btn," in components_css
    assert ".mcp-status-toggle {" in components_css
    assert ".mcp-status-list {" in components_css
    assert ".mcp-status-card {" in components_css
    assert ".mcp-status-card-actions {" in components_css
    assert ".mcp-tools-list {" in components_css
    assert ".mcp-tools-collapsed-summary," in components_css
    assert ".mcp-tool-row {" in components_css
    assert ".mcp-tool-name {" in components_css
    assert ".mcp-tools-error {" in components_css


def _run_system_status_script(
    tmp_path: Path,
    runner_source: str,
    mock_api_source: str = DEFAULT_MOCK_API_SOURCE,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "systemStatus.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "systemStatus.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        mock_api_source,
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
    mock_logger_path.write_text(
        """
export function errorToPayload(error, extra = {}) {
    return {
        error_message: String(error?.message || error || ''),
        ...extra,
    };
}

export function logError(eventName, message, payload) {
    globalThis.__logEntries.push({ eventName, message, payload });
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
function createElement(initialDisplay = 'block') {{
    return {{
        style: {{ display: initialDisplay }},
        innerHTML: '',
        textContent: '',
        value: '',
        onclick: null,
    }};
}}

function createElements() {{
    return new Map([
        ['mcp-status', createElement('block')],
        ['skills-status', createElement('block')],
        ['reload-mcp-btn', createElement('block')],
        ['reload-skills-btn', createElement('block')],
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
    globalThis.__fetchConfigStatusCalls = 0;
    globalThis.__reloadMcpCalls = 0;
    globalThis.__reloadSkillsCalls = 0;
    globalThis.__toolFetchCalls = [];
    globalThis.__toasts = [];
    globalThis.__logEntries = [];
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
