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


def test_model_step_started_tracks_normal_mode_subagents_as_child_sessions(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleModelStepStarted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.currentSessionMode = 'normal';
state.mainAgentRoleId = 'MainAgent';

handleModelStepStarted({ run_id: 'subagent_run_deadbeef' }, 'writer-1', 'writer');

await Promise.resolve();

console.log(JSON.stringify({
    rememberCalls: globalThis.__rememberLiveSubagentCalls,
    refreshCalls: globalThis.__refreshSubagentRailCalls,
    openCalls: globalThis.__openAgentPanelCalls,
    rememberSessionCalls: globalThis.__rememberNormalModeSubagentSessionCalls,
    activeAgentRoleId: state.activeAgentRoleId,
    activeAgentInstanceId: state.activeAgentInstanceId,
}));
""".strip(),
    )

    assert payload["rememberCalls"] == []
    assert payload["refreshCalls"] == []
    assert payload["openCalls"] == []
    assert payload["rememberSessionCalls"] == [
        {
            "sessionId": "session-1",
            "record": {
                "instance_id": "writer-1",
                "role_id": "writer",
                "run_id": "subagent_run_deadbeef",
                "status": "running",
            },
        }
    ]
    assert payload["activeAgentRoleId"] == "writer"
    assert payload["activeAgentInstanceId"] == "writer-1"


def test_route_event_routes_subagent_stream_events_without_overwriting_parent_run(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');
const { state } = await import('./mockState.mjs');

state.activeRunId = 'run-parent';

routeEvent('text_delta', {}, { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' });
routeEvent('token_usage', {}, { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' });

await Promise.resolve();

console.log(JSON.stringify({
    activeRunId: state.activeRunId,
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    tokenUsageCalls: globalThis.__scheduleSessionTokenUsageRefreshCalls,
    runEventCalls: globalThis.__runEventCalls,
}));
""".strip(),
    )

    assert payload["activeRunId"] == "run-parent"
    assert payload["recoveryCalls"] == []
    assert payload["tokenUsageCalls"] == [{"immediate": True}]
    assert payload["runEventCalls"] == [
        {
            "name": "handleTextDelta",
            "args": [
                {},
                {
                    "run_id": "subagent_run_deadbeef",
                    "trace_id": "subagent_run_deadbeef",
                },
                None,
                None,
            ],
        }
    ]


def test_route_event_refreshes_recovery_for_subagent_user_question_events(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');
const { state } = await import('./mockState.mjs');

state.activeRunId = 'run-parent';

routeEvent(
    'user_question_requested',
    { question_id: 'question-1' },
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
);

await Promise.resolve();

console.log(JSON.stringify({
    activeRunId: state.activeRunId,
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    tokenUsageCalls: globalThis.__scheduleSessionTokenUsageRefreshCalls,
    runEventCalls: globalThis.__runEventCalls,
}));
""".strip(),
    )

    assert payload["activeRunId"] == "run-parent"
    assert payload["recoveryCalls"] == [
        {
            "sessionId": "session-1",
            "delayMs": 0,
            "includeRounds": False,
            "quiet": True,
            "reason": "user_question_requested",
        }
    ]
    assert payload["tokenUsageCalls"] == []
    assert payload["runEventCalls"] == []


def test_route_event_routes_fallback_events(tmp_path: Path) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

routeEvent('llm_fallback_activated', { from_profile_id: 'default', to_profile_id: 'secondary' }, { run_id: 'run-1', trace_id: 'run-1' });
routeEvent('llm_fallback_exhausted', { from_profile_id: 'default' }, { run_id: 'run-1', trace_id: 'run-1' });

await Promise.resolve();

console.log(JSON.stringify({
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    runEventCalls: globalThis.__runEventCalls,
}));
""".strip(),
    )

    assert payload["recoveryCalls"] == [
        {
            "sessionId": "session-1",
            "delayMs": 0,
            "includeRounds": False,
            "quiet": True,
            "reason": "llm_fallback_activated",
        },
        {
            "sessionId": "session-1",
            "delayMs": 0,
            "includeRounds": False,
            "quiet": True,
            "reason": "llm_fallback_exhausted",
        },
    ]
    assert payload["runEventCalls"] == [
        {
            "name": "handleLlmFallbackActivated",
            "args": [
                {"from_profile_id": "default", "to_profile_id": "secondary"},
                {"run_id": "run-1", "trace_id": "run-1"},
            ],
        },
        {
            "name": "handleLlmFallbackExhausted",
            "args": [
                {"from_profile_id": "default"},
                {"run_id": "run-1", "trace_id": "run-1"},
            ],
        },
    ]


def test_handle_subagent_run_terminal_finalizes_with_run_id(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleSubagentRunTerminal } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.instanceRoleMap['writer-1'] = 'writer';

handleSubagentRunTerminal(
    'writer-1',
    'completed',
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
    'writer',
);

console.log(JSON.stringify({
    finalizeCalls: globalThis.__finalizeStreamCalls,
    statusCalls: globalThis.__updateNormalModeSubagentSessionStatusCalls,
    settleCalls: globalThis.__settleActiveSubagentSessionAfterTerminalCalls,
}));
""".strip(),
    )

    assert payload["finalizeCalls"] == [
        {
            "instanceId": "writer-1",
            "roleId": "writer",
            "options": {"runId": "subagent_run_deadbeef"},
        }
    ]
    assert payload["statusCalls"] == [
        {
            "sessionId": "session-1",
            "instanceId": "writer-1",
            "status": "completed",
        }
    ]
    assert payload["settleCalls"] == []


def test_handle_subagent_run_terminal_settles_active_child_session(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleSubagentRunTerminal } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.instanceRoleMap['writer-1'] = 'writer';
globalThis.__activeSubagentSession = {
    sessionId: 'session-1',
    instanceId: 'writer-1',
};

handleSubagentRunTerminal(
    'writer-1',
    'completed',
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
    'writer',
);

console.log(JSON.stringify({
    finalizeCalls: globalThis.__finalizeStreamCalls,
    settleCalls: globalThis.__settleActiveSubagentSessionAfterTerminalCalls,
}));
""".strip(),
    )

    assert payload["finalizeCalls"] == [
        {
            "instanceId": "writer-1",
            "roleId": "writer",
            "options": {"runId": "subagent_run_deadbeef"},
        }
    ]
    assert payload["settleCalls"] == ["writer-1"]


def test_handle_fallback_logs_escape_profile_labels(tmp_path: Path) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleLlmFallbackActivated, handleLlmFallbackExhausted } = await import('./runEvents.mjs');

handleLlmFallbackActivated({
    from_profile_id: '<img src=x onerror=1>',
    to_profile_id: '<svg onload=1>',
});
handleLlmFallbackExhausted({
    from_profile_id: '<script>alert(1)</script>',
});

console.log(JSON.stringify({
    sysLogCalls: globalThis.__sysLogCalls,
}));
""".strip(),
    )

    assert payload["sysLogCalls"] == [
        [
            "Fallback activated: &lt;img src=x onerror=1&gt; -> &lt;svg onload=1&gt;",
            "log-info",
        ],
        [
            "Fallback exhausted for &lt;script&gt;alert(1)&lt;/script&gt;.",
            "log-error",
        ],
    ]


def test_handle_model_step_finished_passes_run_id_for_normal_mode_subagent(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleModelStepFinished } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.currentSessionMode = 'normal';
state.mainAgentRoleId = 'MainAgent';
state.instanceRoleMap['writer-1'] = 'writer';
globalThis.__activeSubagentSessionStreamContainer = {};

handleModelStepFinished(
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
    'writer-1',
);

console.log(JSON.stringify({
    finalizeCalls: globalThis.__finalizeStreamCalls,
    statusCalls: globalThis.__updateNormalModeSubagentSessionStatusCalls,
}));
""".strip(),
    )

    assert payload["finalizeCalls"] == [
        {
            "instanceId": "writer-1",
            "roleId": "writer",
            "options": {"runId": "subagent_run_deadbeef"},
        }
    ]
    assert payload["statusCalls"] == [
        {
            "sessionId": "session-1",
            "instanceId": "writer-1",
            "status": "completed",
        }
    ]


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
        "../../components/subagentSessions.js": "./mockSubagentSessions.mjs",
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
    activeSubagentSession: null,
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
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function getActiveSubagentSession() {
    return globalThis.__activeSubagentSession || null;
}

export function getActiveSubagentSessionStreamContainer() {
    return globalThis.__activeSubagentSessionStreamContainer || null;
}

export function rememberNormalModeSubagentSession(sessionId, record) {
    globalThis.__rememberNormalModeSubagentSessionCalls.push({ sessionId, record });
}

export async function renderActiveSubagentSession() {
    globalThis.__renderActiveSubagentSessionCalls.push(true);
}

export function settleActiveSubagentSessionAfterTerminal(instanceId) {
    globalThis.__settleActiveSubagentSessionAfterTerminalCalls.push(instanceId);
}

export function updateNormalModeSubagentSessionStatus(sessionId, instanceId, status) {
    globalThis.__updateNormalModeSubagentSessionStatusCalls.push({
        sessionId,
        instanceId,
        status,
    });
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
    promptInputHint: null,
    chatMessages: null,
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog(...args) {
    globalThis.__sysLogCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function appendThinkingChunk() {
    return undefined;
}

export function applyStreamOverlayEvent() {
    return undefined;
}

export function appendStreamChunk() {
    return undefined;
}

export function appendStreamOutputParts() {
    return undefined;
}

export function finalizeThinking() {
    return undefined;
}

export function finalizeStream(instanceId, roleId = '', options = null) {
    globalThis.__finalizeStreamCalls.push({ instanceId, roleId, options });
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
globalThis.__rememberNormalModeSubagentSessionCalls = [];
globalThis.__renderActiveSubagentSessionCalls = [];
globalThis.__updateNormalModeSubagentSessionStatusCalls = [];
globalThis.__finalizeStreamCalls = [];
globalThis.__settleActiveSubagentSessionAfterTerminalCalls = [];
globalThis.__sysLogCalls = [];
globalThis.__activeSubagentSession = null;
globalThis.__activeSubagentSessionStreamContainer = null;

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
        timeout=3,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)


def _run_event_router_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "core" / "eventRouter" / "index.js"
    )

    module_under_test_path = tmp_path / "eventRouterIndex.mjs"
    runner_path = tmp_path / "runner-event-router.mjs"

    replacements = {
        "../../app/recovery.js": "./mockRecovery.mjs",
        "../../components/rounds.js": "./mockRounds.mjs",
        "../../components/sessionTokenUsage.js": "./mockSessionTokenUsage.mjs",
        "../state.js": "./mockState.mjs",
        "../../utils/logger.js": "./mockLogger.mjs",
        "./runEvents.js": "./mockRunEvents.mjs",
        "./toolEvents.js": "./mockToolEvents.mjs",
        "./humanEvents.js": "./mockHumanEvents.mjs",
        "./notificationEvents.js": "./mockNotificationEvents.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
    activeRunId: null,
    taskInstanceMap: {},
    taskStatusMap: {},
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function scheduleRecoveryContinuityRefresh(options) {
    globalThis.__scheduleRecoveryContinuityRefreshCalls.push(options);
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSessionTokenUsage.mjs").write_text(
        """
export function scheduleSessionTokenUsageRefresh(options) {
    globalThis.__scheduleSessionTokenUsageRefreshCalls.push(options);
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRounds.mjs").write_text(
        """
export function syncRoundTodoVisibility() {
    return undefined;
}

export function updateRoundTodo() {
    return undefined;
}
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
    (tmp_path / "mockRunEvents.mjs").write_text(
        """
function pushCall(name, args) {
    globalThis.__runEventCalls.push({ name, args });
}

export function handleLlmRetryExhausted(...args) { pushCall('handleLlmRetryExhausted', args); }
export function handleLlmRetryScheduled(...args) { pushCall('handleLlmRetryScheduled', args); }
export function handleLlmFallbackActivated(...args) { pushCall('handleLlmFallbackActivated', args); }
export function handleLlmFallbackExhausted(...args) { pushCall('handleLlmFallbackExhausted', args); }
export function handleModelStepFinished(...args) { pushCall('handleModelStepFinished', args); }
export function handleModelStepStarted(...args) { pushCall('handleModelStepStarted', args); }
export function handleOutputDelta(...args) { pushCall('handleOutputDelta', args); }
export function handleGenerationProgress(...args) { pushCall('handleGenerationProgress', args); }
export function handleRunCompleted(...args) { pushCall('handleRunCompleted', args); }
export function handleRunFailed(...args) { pushCall('handleRunFailed', args); }
export function handleRunStarted(...args) { pushCall('handleRunStarted', args); }
export function handleRunStopped(...args) { pushCall('handleRunStopped', args); }
export function handleSubagentRunTerminal(...args) { pushCall('handleSubagentRunTerminal', args); }
export function handleThinkingDelta(...args) { pushCall('handleThinkingDelta', args); }
export function handleThinkingFinished(...args) { pushCall('handleThinkingFinished', args); }
export function handleThinkingStarted(...args) { pushCall('handleThinkingStarted', args); }
export function handleTextDelta(...args) { pushCall('handleTextDelta', args); }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockToolEvents.mjs").write_text(
        """
export function handleToolApprovalRequested() { return undefined; }
export function handleToolApprovalResolved() { return undefined; }
export function handleToolCall() { return undefined; }
export function handleToolInputValidationFailed() { return undefined; }
export function handleToolResult() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockHumanEvents.mjs").write_text(
        """
export function handleAwaitingHumanDispatch() { return undefined; }
export function handleGateResolved() { return undefined; }
export function handleHumanTaskDispatched() { return undefined; }
export function handleSubagentGate() { return undefined; }
export function handleSubagentResumed() { return undefined; }
export function handleSubagentStopped() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNotificationEvents.mjs").write_text(
        """
export function handleNotificationRequested() { return undefined; }
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
globalThis.__scheduleRecoveryContinuityRefreshCalls = [];
globalThis.__scheduleSessionTokenUsageRefreshCalls = [];
globalThis.__runEventCalls = [];

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
        timeout=3,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
