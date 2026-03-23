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
                "userRoleLabel": "Task Prompt",
            },
        }
    ]
