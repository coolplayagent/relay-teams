# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_load_agent_history_uses_task_prompt_label_for_subagent_messages(
    tmp_path: Path,
) -> None:
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
        """
export async function fetchAgentMessages() {
    return [
        {
            role: 'user',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [
                    {
                        part_kind: 'user-prompt',
                        content: 'Draft the response.',
                    },
                ],
            },
        },
    ];
}

export async function fetchAgentReflection() {
    return { summary: 'Reflection', updated_at: '2026-03-16T08:30:00Z' };
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
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
            runtime_tools_json: '{"local_tools":[],"skill_tools":[],"mcp_tools":[]}',
        },
    ],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function getInstanceStreamOverlay() {
    return null;
}

export function bindStreamOverlayToContainer() {
    return null;
}

export function renderHistoricalMessageList(container, messages, options = {}) {
    globalThis.__renderHistoricalMessageListCalls.push({
        messageCount: messages.length,
        options,
    });
}
""".strip(),
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
    "subagent.no_reflection": "No reflection yet",
    "subagent.no_reflection_memory": "No reflection memory yet.",
    "subagent.no_tasks": "No delegated tasks yet.",
    "subagent.task": "Task",
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
        """
globalThis.__renderHistoricalMessageListCalls = [];

const { loadAgentHistory } = await import('./history.mjs');
const { state } = await import('./mockState.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

state.currentSessionId = 'session-1';

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: '',
    loadedRunId: '',
});

await loadAgentHistory('inst-1', 'writer');

console.log(JSON.stringify(globalThis.__renderHistoricalMessageListCalls));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-reflection-meta', createNode()],
            ['.agent-panel-reflection-body', createNode()],
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload == [
        {
            "messageCount": 1,
            "options": {
                "pendingToolApprovals": [],
                "runId": "",
                "streamOverlayEntry": None,
                "separateOverlayMessage": False,
                "userRoleLabel": "Task Prompt",
            },
        }
    ]


def test_render_instance_history_binds_overlay_instead_of_replaying_when_history_exists(
    tmp_path: Path,
) -> None:
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
    runner_path = tmp_path / "runner_bind_overlay.mjs"

    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "./state.js": "./mockPanelState.mjs",
    }.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [
        {
            role: 'assistant',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [{ part_kind: 'text', content: 'already persisted' }],
            },
        },
    ];
}

export async function fetchAgentReflection() {
    return null;
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
    activeRunId: null,
    currentRecoverySnapshot: null,
    sessionTasks: [],
    sessionAgents: [],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function getInstanceStreamOverlay() {
    return {
        roleId: 'writer',
        instanceId: 'inst-1',
        label: 'Writer',
        parts: [{ kind: 'text', content: 'already persisted' }],
        textStreaming: true,
    };
}

export function bindStreamOverlayToContainer(container, options = {}) {
    globalThis.__bindCalls.push({
        containerId: container.id || '',
        instanceId: options.instanceId || '',
        roleId: options.roleId || '',
        runId: options.runId || '',
    });
}

export function renderHistoricalMessageList(container, messages, options = {}) {
    globalThis.__renderCalls.push({
        containerId: container.id || '',
        messageCount: messages.length,
        streamOverlayEntry: options.streamOverlayEntry || null,
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
export function getActiveInstanceId() {
    return null;
}

export function getActiveRoundRunId() {
    return '';
}

export function getPendingApprovalsForPanel() {
    return [];
}

export function getPanel() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__bindCalls = [];
globalThis.__renderCalls = [];

const { renderInstanceHistoryInto } = await import('./history.mjs');

const container = {
    id: 'subagent-body',
    innerHTML: '',
    dataset: {},
};

await renderInstanceHistoryInto(container, {
    sessionId: 'session-1',
    instanceId: 'inst-1',
    roleId: 'writer',
    runId: 'subagent_run_1',
    overlayMode: 'bind',
});

console.log(JSON.stringify({
    renderCalls: globalThis.__renderCalls,
    bindCalls: globalThis.__bindCalls,
}));
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload["renderCalls"] == [
        {
            "containerId": "subagent-body",
            "messageCount": 1,
            "streamOverlayEntry": None,
        }
    ]
    assert payload["bindCalls"] == [
        {
            "containerId": "subagent-body",
            "instanceId": "inst-1",
            "roleId": "writer",
            "runId": "subagent_run_1",
        }
    ]


def test_render_instance_history_defers_terminal_repaint_until_tool_results_are_persisted(
    tmp_path: Path,
) -> None:
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
    runner_path = tmp_path / "runner_deferred_terminal.mjs"

    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "./state.js": "./mockPanelState.mjs",
    }.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [
        {
            role: 'assistant',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [
                    {
                        part_kind: 'tool-call',
                        tool_name: 'shell',
                        tool_call_id: 'call-1',
                        args: { command: 'sleep 60' },
                    },
                ],
            },
        },
    ];
}

export async function fetchAgentReflection() {
    return null;
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
    activeRunId: null,
    currentRecoverySnapshot: null,
    sessionTasks: [],
    sessionAgents: [],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function getInstanceStreamOverlay() {
    return null;
}

export function bindStreamOverlayToContainer() {
    return null;
}

export function renderHistoricalMessageList() {
    globalThis.__renderCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
export function getActiveInstanceId() {
    return null;
}

export function getActiveRoundRunId() {
    return '';
}

export function getPendingApprovalsForPanel() {
    return [];
}

export function getPanel() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__renderCalls = 0;

const { renderInstanceHistoryInto } = await import('./history.mjs');

const container = {
    innerHTML: 'existing-live-dom',
    dataset: {},
};

const result = await renderInstanceHistoryInto(container, {
    sessionId: 'session-1',
    instanceId: 'inst-1',
    runId: 'subagent_run_1',
    requireToolBoundary: true,
});

console.log(JSON.stringify({
    result,
    innerHTML: container.innerHTML,
    renderCalls: globalThis.__renderCalls,
}));
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload["result"]["deferred"] is True
    assert payload["innerHTML"] == "existing-live-dom"
    assert payload["renderCalls"] == 0


def test_render_instance_history_uses_separate_overlay_for_running_child_session(
    tmp_path: Path,
) -> None:
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
    runner_path = tmp_path / "runner_separate_overlay.mjs"

    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "./state.js": "./mockPanelState.mjs",
    }.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [
        {
            role: 'assistant',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [{ part_kind: 'text', content: 'already persisted' }],
            },
        },
    ];
}

export async function fetchAgentReflection() {
    return null;
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
    activeRunId: null,
    currentRecoverySnapshot: null,
    sessionTasks: [],
    sessionAgents: [],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function getInstanceStreamOverlay() {
    return {
        roleId: 'writer',
        instanceId: 'inst-1',
        label: 'Writer',
        parts: [{ kind: 'text', content: 'live tail' }],
        textStreaming: true,
    };
}

export function bindStreamOverlayToContainer() {
    globalThis.__bindCalls += 1;
}

export function renderHistoricalMessageList(_container, messages, options = {}) {
    globalThis.__renderCalls.push({
        messageCount: messages.length,
        separateOverlayMessage: options.separateOverlayMessage === true,
        streamOverlayEntry: options.streamOverlayEntry || null,
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
export function getActiveInstanceId() {
    return null;
}

export function getActiveRoundRunId() {
    return '';
}

export function getPendingApprovalsForPanel() {
    return [];
}

export function getPanel() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__renderCalls = [];
globalThis.__bindCalls = 0;

const { renderInstanceHistoryInto } = await import('./history.mjs');

await renderInstanceHistoryInto(
    {
        innerHTML: '',
        dataset: {},
    },
    {
        sessionId: 'session-1',
        instanceId: 'inst-1',
        runId: 'subagent_run_1',
        roleId: 'writer',
        overlayMode: 'separate',
        status: 'running',
        runStatus: 'running',
    },
);

console.log(JSON.stringify({
    renderCalls: globalThis.__renderCalls,
    bindCalls: globalThis.__bindCalls,
}));
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload["renderCalls"] == [
        {
            "messageCount": 1,
            "separateOverlayMessage": True,
            "streamOverlayEntry": {
                "roleId": "writer",
                "instanceId": "inst-1",
                "label": "Writer",
                "parts": [{"kind": "text", "content": "live tail"}],
                "textStreaming": True,
            },
        }
    ]
    assert payload["bindCalls"] == 0


def test_render_instance_history_ignores_overlay_for_completed_child_session(
    tmp_path: Path,
) -> None:
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
    runner_path = tmp_path / "runner_completed_overlay.mjs"

    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "./state.js": "./mockPanelState.mjs",
    }.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [
        {
            role: 'assistant',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [{ part_kind: 'text', content: 'persisted only' }],
            },
        },
    ];
}

export async function fetchAgentReflection() {
    return null;
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
    activeRunId: null,
    currentRecoverySnapshot: null,
    sessionTasks: [],
    sessionAgents: [],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function getInstanceStreamOverlay() {
    return {
        roleId: 'writer',
        instanceId: 'inst-1',
        label: 'Writer',
        parts: [{ kind: 'text', content: 'stale live tail' }],
        textStreaming: true,
    };
}

export function bindStreamOverlayToContainer() {
    globalThis.__bindCalls += 1;
}

export function renderHistoricalMessageList(_container, messages, options = {}) {
    globalThis.__renderCalls.push({
        messageCount: messages.length,
        separateOverlayMessage: options.separateOverlayMessage === true,
        streamOverlayEntry: options.streamOverlayEntry || null,
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
export function getActiveInstanceId() {
    return null;
}

export function getActiveRoundRunId() {
    return '';
}

export function getPendingApprovalsForPanel() {
    return [];
}

export function getPanel() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__renderCalls = [];
globalThis.__bindCalls = 0;

const { renderInstanceHistoryInto } = await import('./history.mjs');

await renderInstanceHistoryInto(
    {
        innerHTML: '',
        dataset: {},
    },
    {
        sessionId: 'session-1',
        instanceId: 'inst-1',
        runId: 'subagent_run_1',
        roleId: 'writer',
        overlayMode: 'separate',
        status: 'completed',
        runStatus: 'completed',
        runPhase: 'terminal',
    },
);

console.log(JSON.stringify({
    renderCalls: globalThis.__renderCalls,
    bindCalls: globalThis.__bindCalls,
}));
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload["renderCalls"] == [
        {
            "messageCount": 1,
            "separateOverlayMessage": True,
            "streamOverlayEntry": None,
        }
    ]
    assert payload["bindCalls"] == 0


def test_load_agent_history_passes_marker_entries_to_renderer(tmp_path: Path) -> None:
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
        """
export async function fetchAgentMessages() {
    return [
        {
            entry_type: 'message',
            role: 'user',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [{ part_kind: 'user-prompt', content: 'Before marker.' }],
            },
        },
        {
            entry_type: 'marker',
            marker_type: 'compaction',
            label: 'History compacted',
            created_at: '2026-03-16T08:10:00Z',
        },
    ];
}

export async function fetchAgentReflection() {
    return { summary: 'Reflection', updated_at: '2026-03-16T08:30:00Z' };
}

export async function fetchRunTokenUsage() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
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
            runtime_tools_json: '{"local_tools":[],"skill_tools":[],"mcp_tools":[]}',
        },
    ],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function getInstanceStreamOverlay() {
    return null;
}

export function bindStreamOverlayToContainer() {
    return null;
}

export function renderHistoricalMessageList(container, messages) {
    globalThis.__renderHistoricalMessageListCalls.push({
        entryTypes: messages.map(item => item.entry_type || 'message'),
    });
}
""".strip(),
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
    "subagent.no_reflection": "No reflection yet",
    "subagent.no_reflection_memory": "No reflection memory yet.",
    "subagent.no_tasks": "No delegated tasks yet.",
    "subagent.task": "Task",
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
        """
globalThis.__renderHistoricalMessageListCalls = [];

const { loadAgentHistory } = await import('./history.mjs');
const { setPanel } = await import('./mockPanelState.mjs');

const panelEl = createPanelElement();
setPanel('inst-1', {
    panelEl,
    scrollEl: panelEl.querySelector('.agent-panel-scroll'),
    loadedSessionId: '',
    loadedRunId: '',
});

await loadAgentHistory('inst-1', 'writer');

console.log(JSON.stringify(globalThis.__renderHistoricalMessageListCalls));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
            ['.agent-panel-runtime-prompt-meta', createNode()],
            ['.agent-panel-runtime-prompt-body', createNode()],
            ['.agent-panel-runtime-tools-meta', createNode()],
            ['.agent-panel-runtime-tools-body', createNode()],
            ['.agent-panel-reflection-meta', createNode()],
            ['.agent-panel-reflection-body', createNode()],
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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload == [{"entryTypes": ["message", "marker"]}]
