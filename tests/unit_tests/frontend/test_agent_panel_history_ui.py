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
}));

function createPanelElement() {
    return {
        _nodes: new Map([
            ['.agent-panel-scroll', createNode()],
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
    };
}
""".strip(),
    )

    tasks_html = cast(str, payload["tasksHtml"])
    assert tasks_html.index("Newer completed task") < tasks_html.index(
        "Older completed task"
    )


def _run_history_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
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
    return [];
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

export function renderHistoricalMessageList() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
const panels = new Map();

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

    runner_path.write_text(runner_source, encoding="utf-8")

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
