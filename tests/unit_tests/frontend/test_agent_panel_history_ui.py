# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


def test_agent_panel_summary_orders_completed_tasks_by_updated_at_desc(
    tmp_path: Path,
) -> None:
    payload = _run_history_script(
        tmp_path=tmp_path,
        runner_source="""
const { loadAgentHistory } = await import('./history.mjs');
const { state } = await import('./mockState.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

state.currentSessionId = 'session-1';
state.sessionTasks = [
    {
        task_id: 'task-older',
        title: 'Older completed task',
        role_id: 'writer',
        status: 'completed',
        instance_id: 'inst-1',
        run_id: 'run-1',
        updated_at: '2026-03-16T08:10:00Z',
    },
    {
        task_id: 'task-newer',
        title: 'Newer completed task',
        role_id: 'writer',
        status: 'completed',
        instance_id: 'inst-1',
        run_id: 'run-1',
        updated_at: '2026-03-16T08:20:00Z',
    },
];

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: '',
    loadedRunId: '',
});

await loadAgentHistory('inst-1', 'writer');

console.log(JSON.stringify({
    tasksHtml: panelEl.querySelector('.agent-panel-summary-tasks').innerHTML,
    promptHtml: panelEl.querySelector('.agent-panel-runtime-prompt-body').innerHTML,
    promptMeta: panelEl.querySelector('.agent-panel-runtime-prompt-meta').textContent,
    toolsHtml: panelEl.querySelector('.agent-panel-runtime-tools-body').innerHTML,
    toolsMeta: panelEl.querySelector('.agent-panel-runtime-tools-meta').textContent,
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-memory-meta', createNode()],
            ['.agent-panel-memory-body', createNode()],
            ['.agent-panel-summary-status', createNode()],
            ['.agent-panel-summary-updated', createNode()],
            ['.agent-panel-summary-tasks', createNode()],
            ['.agent-token-usage[data-instance-id="inst-1"]', createNode()],
        ]),
        querySelector(selector) {
            return this._nodes.get(selector) || null;
        },
    };
}

function createNode() {
    return {
        innerHTML: '',
        textContent: '',
        className: '',
        dataset: {},
    };
}
""".strip(),
    )

    tasks_html = cast(str, payload["tasksHtml"])
    assert tasks_html.index("Newer completed task") < tasks_html.index(
        "Older completed task"
    )
    assert "You are the runtime writer." in cast(str, payload["promptHtml"])
    assert payload["promptMeta"] == "1 lines"
    assert "local_tools" in cast(str, payload["toolsHtml"])
    assert payload["toolsMeta"] == "1 tools"


def test_sync_agent_panel_state_renders_runtime_snapshot_from_session_state(
    tmp_path: Path,
) -> None:
    payload = _run_history_script(
        tmp_path=tmp_path,
        runner_source="""
const { syncAgentPanelState } = await import('./history.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: 'session-1',
    loadedRunId: 'run-1',
});

syncAgentPanelState('inst-1', 'writer');

console.log(JSON.stringify({
    promptHtml: panelEl.querySelector('.agent-panel-runtime-prompt-body').innerHTML,
    promptMeta: panelEl.querySelector('.agent-panel-runtime-prompt-meta').textContent,
    toolsHtml: panelEl.querySelector('.agent-panel-runtime-tools-body').innerHTML,
    toolsMeta: panelEl.querySelector('.agent-panel-runtime-tools-meta').textContent,
    summaryStatus: panelEl.querySelector('.agent-panel-summary-status').textContent,
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-memory-meta', createNode()],
            ['.agent-panel-memory-body', createNode()],
            ['.agent-panel-summary-status', createNode()],
            ['.agent-panel-summary-updated', createNode()],
            ['.agent-panel-summary-tasks', createNode()],
            ['.agent-token-usage[data-instance-id="inst-1"]', createNode()],
        ]),
        querySelector(selector) {
            return this._nodes.get(selector) || null;
        },
    };
}

function createNode() {
    return {
        innerHTML: '',
        textContent: '',
        className: '',
        dataset: {},
    };
}
""".strip(),
    )

    assert "You are the runtime writer." in cast(str, payload["promptHtml"])
    assert payload["promptMeta"] == "1 lines"
    assert "local_tools" in cast(str, payload["toolsHtml"])
    assert payload["toolsMeta"] == "1 tools"
    assert payload["summaryStatus"] == "Completed"


def test_agent_panel_summary_shows_spec_and_evidence_bundle(
    tmp_path: Path,
) -> None:
    payload = _run_history_script(
        tmp_path=tmp_path,
        runner_source="""
const { syncAgentPanelState } = await import('./history.mjs');
const { state } = await import('./mockState.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

state.sessionTasks = [
    {
        task_id: 'task-spec',
        title: 'Implement contract',
        role_id: 'writer',
        status: 'completed',
        instance_id: 'inst-1',
        run_id: 'run-1',
        updated_at: '2026-03-16T08:20:00Z',
        spec_artifact_id: 'spec-1234567890abcdef',
        spec_strictness: 'high',
        evidence_bundle: {
            items: [
                { passed: true },
                { passed: false },
                { passed: true },
            ],
        },
    },
];

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: 'session-1',
    loadedRunId: 'run-1',
});

syncAgentPanelState('inst-1', 'writer');

console.log(JSON.stringify({
    tasksHtml: panelEl.querySelector('.agent-panel-summary-tasks').innerHTML,
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-memory-meta', createNode()],
            ['.agent-panel-memory-body', createNode()],
            ['.agent-panel-summary-status', createNode()],
            ['.agent-panel-summary-updated', createNode()],
            ['.agent-panel-summary-tasks', createNode()],
        ]),
        querySelector(selector) {
            return this._nodes.get(selector) || null;
        },
    };
}

function createNode() {
    return {
        innerHTML: '',
        textContent: '',
        className: '',
        dataset: {},
    };
}
""".strip(),
    )

    tasks_html = cast(str, payload["tasksHtml"])
    assert "Spec: spec-1234...cdef / high" in tasks_html
    assert "Evidence: 2/3" in tasks_html


def test_sync_agent_panel_state_hides_coordinator_tools_for_non_coordinator(
    tmp_path: Path,
) -> None:
    payload = _run_history_script(
        tmp_path=tmp_path,
        runner_source="""
const { syncAgentPanelState } = await import('./history.mjs');
const { state } = await import('./mockState.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

state.coordinatorRoleId = 'Coordinator';
state.sessionAgents = [
    {
        instance_id: 'inst-1',
        role_id: 'writer',
        status: 'completed',
        runtime_system_prompt: 'You are the runtime writer.',
        runtime_tools_json: JSON.stringify({
            local_tools: [
                { source: 'local', name: 'read' },
                { source: 'local', name: 'orch_dispatch_task' },
                'orch_update_task',
            ],
            skill_tools: [],
            mcp_tools: [],
        }),
    },
];

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: 'session-1',
    loadedRunId: 'run-1',
});

syncAgentPanelState('inst-1', 'writer');

console.log(JSON.stringify({
    toolsHtml: panelEl.querySelector('.agent-panel-runtime-tools-body').innerHTML,
    toolsMeta: panelEl.querySelector('.agent-panel-runtime-tools-meta').textContent,
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-memory-meta', createNode()],
            ['.agent-panel-memory-body', createNode()],
            ['.agent-panel-summary-status', createNode()],
            ['.agent-panel-summary-updated', createNode()],
            ['.agent-panel-summary-tasks', createNode()],
            ['.agent-token-usage[data-instance-id="inst-1"]', createNode()],
        ]),
        querySelector(selector) {
            return this._nodes.get(selector) || null;
        },
    };
}

function createNode() {
    return {
        innerHTML: '',
        textContent: '',
        className: '',
        dataset: {},
    };
}
""".strip(),
    )

    tools_html = cast(str, payload["toolsHtml"])
    assert "read" in tools_html
    assert "orch_dispatch_task" not in tools_html
    assert "orch_update_task" not in tools_html
    assert payload["toolsMeta"] == "1 tools"


def test_load_agent_history_keeps_rendered_history_when_auxiliary_fetch_fails(
    tmp_path: Path,
) -> None:
    payload = _run_history_script(
        tmp_path=tmp_path,
        mock_api_source="""
export async function fetchAgentMessages() {
    return [
        {
            role: 'assistant',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [{ part_kind: 'text', content: 'persisted history' }],
            },
        },
    ];
}

export async function fetchMemories() {
    throw new Error('memory unavailable');
}

export async function fetchRunTokenUsage() {
    throw new Error('usage unavailable');
}
""".strip(),
        mock_message_renderer_source="""
export function getInstanceStreamOverlay() {
    return null;
}

export function bindStreamOverlayToContainer() {
    return null;
}

export function renderHistoricalMessageList(container, messages) {
    container.innerHTML = `rendered:${messages.length}`;
}
""".strip(),
        runner_source="""
const { loadAgentHistory } = await import('./history.mjs');
const { state } = await import('./mockState.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: '',
    loadedRunId: '',
});

await loadAgentHistory('inst-1', 'writer');

console.log(JSON.stringify({
    scrollHtml: panelEl.querySelector('.agent-panel-scroll').innerHTML,
    loadedSessionId: panelEl.__panelRecord?.loadedSessionId || '',
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-memory-meta', createNode()],
            ['.agent-panel-memory-body', createNode()],
            ['.agent-panel-summary-status', createNode()],
            ['.agent-panel-summary-updated', createNode()],
            ['.agent-panel-summary-tasks', createNode()],
            ['.agent-token-usage[data-instance-id="inst-1"]', createNode()],
        ]),
        querySelector(selector) {
            return this._nodes.get(selector) || null;
        },
    };
}

function createNode() {
    return {
        innerHTML: '',
        textContent: '',
        className: '',
        dataset: {},
    };
}
""".strip(),
    )

    assert payload["scrollHtml"] == "rendered:1"


def test_load_agent_history_clears_loading_placeholder_before_rendering(
    tmp_path: Path,
) -> None:
    payload = _run_history_script(
        tmp_path=tmp_path,
        mock_api_source="""
export async function fetchAgentMessages() {
    return [
        {
            role: 'assistant',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [{ part_kind: 'text', content: 'persisted history' }],
            },
        },
    ];
}

export async function fetchMemories() {
    return { summary: 'Memory', updated_at: '2026-03-16T08:30:00Z' };
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
        mock_message_renderer_source="""
export function getInstanceStreamOverlay() {
    return null;
}

export function bindStreamOverlayToContainer() {
    return null;
}

export function renderHistoricalMessageList(container, messages) {
    container.innerHTML += `rendered:${messages.length}`;
}
""".strip(),
        runner_source="""
const { loadAgentHistory } = await import('./history.mjs');
const { state } = await import('./mockState.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: '',
    loadedRunId: '',
});

await loadAgentHistory('inst-1', 'writer');

console.log(JSON.stringify({
    scrollHtml: panelEl.querySelector('.agent-panel-scroll').innerHTML,
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-memory-meta', createNode()],
            ['.agent-panel-memory-body', createNode()],
            ['.agent-panel-summary-status', createNode()],
            ['.agent-panel-summary-updated', createNode()],
            ['.agent-panel-summary-tasks', createNode()],
            ['.agent-token-usage[data-instance-id="inst-1"]', createNode()],
        ]),
        querySelector(selector) {
            return this._nodes.get(selector) || null;
        },
    };
}

function createNode() {
    return {
        innerHTML: '',
        textContent: '',
        className: '',
        dataset: {},
    };
}
""".strip(),
    )

    assert payload["scrollHtml"] == "rendered:1"


def _run_history_script(
    tmp_path: Path,
    runner_source: str,
    *,
    mock_api_source: str | None = None,
    mock_message_renderer_source: str | None = None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "agentPanel"
        / "history.js"
    )

    module_under_test_path = tmp_path / "history.mjs"
    runner_path = tmp_path / "runner.mjs"

    replacements = {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "./state.js": "./mockPanelState.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        (
            mock_api_source
            or """
export async function fetchAgentMessages() {
    return [];
}

export async function fetchMemories() {
    return { summary: 'Memory', updated_at: '2026-03-16T08:30:00Z' };
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip()
        ),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: null,
    activeRunId: null,
    currentRecoverySnapshot: null,
    sessionTasks: [],
    sessionAgents: [
        {
            instance_id: 'inst-1',
            role_id: 'writer',
            status: 'completed',
            updated_at: '2026-03-16T08:20:00Z',
            created_at: '2026-03-16T08:00:00Z',
            runtime_system_prompt: 'You are the runtime writer.',
            runtime_tools_json: '{"local_tools":[{"source":"local","name":"read","description":"Read a file or directory from disk.","server_name":"","kind":"function","strict":null,"sequential":false,"parameters_json_schema":{}}],"skill_tools":[],"mcp_tools":[]}',
        },
    ],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        (
            mock_message_renderer_source
            or """
export function getInstanceStreamOverlay() {
    return null;
}

export function bindStreamOverlayToContainer() {
    return null;
}

export function renderHistoricalMessageList() {
    return undefined;
}
""".strip()
        ),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
const translations = {
    "subagent.no_snapshot": "No snapshot yet",
    "subagent.no_runtime_prompt": "No runtime system prompt yet.",
    "subagent.no_runtime_tools": "No runtime tools snapshot yet.",
    "subagent.prompt_lines": "{count} lines",
    "subagent.tools_count": "{count} tools",
    "subagent.json_snapshot": "JSON snapshot",
    "subagent.memory_count": "{count} memories",
    "subagent.memory_empty": "No memory yet.",
    "subagent.no_tasks": "No delegated tasks yet.",
    "subagent.task": "Task",
    "subagent.spec": "Spec",
    "subagent.spec_bound": "bound",
    "subagent.evidence": "Evidence",
    "subagent.task_prompt": "Task Prompt",
    "subagent.status_idle": "Idle",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
const panels = new Map();

export function getActiveInstanceId() {
    return null;
}

export function getActiveRoundRunId() {
    return '';
}

export function getPendingApprovalsForPanel() {
    return [];
}

export function getPanel(instanceId) {
    return panels.get(instanceId) || null;
}

export function setPanel(instanceId, panel) {
    panels.set(instanceId, panel);
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
globalThis.__renderHistoricalMessageListCalls = [];

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
        encoding="utf-8",
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            f"Node runner failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
