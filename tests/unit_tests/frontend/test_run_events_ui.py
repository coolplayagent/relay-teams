# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_model_step_started_refreshes_subagent_runtime_snapshot(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleModelStepStarted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.currentSessionMode = 'orchestration';
state.coordinatorRoleId = 'Coordinator';

handleModelStepStarted({}, 'writer-1', 'writer');

await Promise.resolve();

console.log(JSON.stringify({
    rememberCalls: globalThis.__rememberLiveSubagentCalls,
    refreshCalls: globalThis.__refreshSubagentRailCalls,
    openCalls: globalThis.__openAgentPanelCalls,
    instanceRoleMap: state.instanceRoleMap,
    roleInstanceMap: state.roleInstanceMap,
    activeAgentRoleId: state.activeAgentRoleId,
    activeAgentInstanceId: state.activeAgentInstanceId,
}));
""".strip(),
    )

    assert payload["rememberCalls"] == [{"instanceId": "writer-1", "roleId": "writer"}]
    assert payload["refreshCalls"] == [
        {
            "sessionId": "session-1",
            "options": {"preserveSelection": True},
        }
    ]
    assert payload["openCalls"] == [{"instanceId": "writer-1", "roleId": "writer"}]
    assert payload["instanceRoleMap"] == {"writer-1": "writer"}
    assert payload["roleInstanceMap"] == {"writer": "writer-1"}
    assert payload["activeAgentRoleId"] == "writer"
    assert payload["activeAgentInstanceId"] == "writer-1"


def _run_run_events_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "eventRouter" / "runEvents.js"
    )

    module_under_test_path = tmp_path / "runEvents.mjs"
    runner_path = tmp_path / "runner.mjs"

    replacements = {
        "../state.js": "./mockState.mjs",
        "../../app/recovery.js": "./mockRecovery.mjs",
        "../../app/retryStatus.js": "./mockRetryStatus.mjs",
        "../../components/subagentRail.js": "./mockSubagentRail.mjs",
        "../../utils/dom.js": "./mockDom.mjs",
        "../../utils/logger.js": "./mockLogger.mjs",
        "../../components/messageRenderer.js": "./mockMessageRenderer.mjs",
        "../../components/agentPanel.js": "./mockAgentPanel.mjs",
        "./utils.js": "./mockUtils.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: null,
    currentSessionMode: 'normal',
    coordinatorRoleId: null,
    mainAgentRoleId: null,
    activeRunId: null,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    autoSwitchedSubagentInstances: {},
    instanceRoleMap: {},
    roleInstanceMap: {},
};

export function getPrimaryRoleId(sessionMode = state.currentSessionMode) {
    return sessionMode === 'orchestration'
        ? String(state.coordinatorRoleId || '')
        : String(state.mainAgentRoleId || '');
}

export function getPrimaryRoleLabel(sessionMode = state.currentSessionMode) {
    return sessionMode === 'orchestration' ? 'Coordinator' : 'Main Agent';
}

export function isPrimaryRoleId(roleId, sessionMode = state.currentSessionMode) {
    const safeRoleId = String(roleId || '').trim();
    return !!safeRoleId && safeRoleId === getPrimaryRoleId(sessionMode);
}

export function getRunPrimaryRoleId() {
    return getPrimaryRoleId();
}

export function getRunPrimaryRoleLabel() {
    return getPrimaryRoleLabel();
}

export function isRunPrimaryRoleId(roleId) {
    return isPrimaryRoleId(roleId);
}

export function clearRunPrimaryRole() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function markRunStreamConnected() {
    return undefined;
}

export function markRunTerminalState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRetryStatus.mjs").write_text(
        """
export function beginLlmRetryAttempt() {
    return undefined;
}

export function clearLlmRetryStatus() {
    return undefined;
}

export function markLlmRetryFailed() {
    return undefined;
}

export function markLlmRetrySucceeded() {
    return undefined;
}

export function showLlmRetryStatus() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentRail.mjs").write_text(
        """
export function rememberLiveSubagent(instanceId, roleId) {
    globalThis.__rememberLiveSubagentCalls.push({ instanceId, roleId });
}

export async function refreshSubagentRail(sessionId, options = {}) {
    globalThis.__refreshSubagentRailCalls.push({ sessionId, options });
}

export function markSubagentStatus() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    sendBtn: null,
    stopBtn: null,
    promptInput: null,
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function appendThinkingChunk() {
    return undefined;
}

export function appendStreamChunk() {
    return undefined;
}

export function finalizeThinking() {
    return undefined;
}

export function finalizeStream() {
    return undefined;
}

export function getOrCreateStreamBlock() {
    return undefined;
}

export function startThinkingBlock() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function getActiveInstanceId() {
    return null;
}

export function getPanelScrollContainer() {
    return {};
}

export function openAgentPanel(instanceId, roleId) {
    globalThis.__openAgentPanelCalls.push({ instanceId, roleId });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockUtils.mjs").write_text(
        """
export function coordinatorContainerFor() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
globalThis.__rememberLiveSubagentCalls = [];
globalThis.__refreshSubagentRailCalls = [];
globalThis.__openAgentPanelCalls = [];

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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
