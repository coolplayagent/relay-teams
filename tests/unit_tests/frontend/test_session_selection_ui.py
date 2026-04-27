# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import json
import subprocess


def test_select_session_ignores_stale_same_session_async_result(
    tmp_path: Path,
) -> None:
    payload = _run_session_script(
        tmp_path=tmp_path,
        runner_source="""
import { selectSession } from "./session.mjs";
import { state } from "./mockState.mjs";

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};

function flushMicrotasks() {
    return Promise.resolve().then(() => Promise.resolve());
}

const firstA = selectSession("session-a");
await flushMicrotasks();
const sessionB = selectSession("session-b");
await flushMicrotasks();
const secondA = selectSession("session-a");
await flushMicrotasks();

globalThis.__hydrateResolvers[0].resolve();
await flushMicrotasks();
const selectedAfterOldA = globalThis.__documentDispatches
    .filter(event => event.type === "agent-teams-session-selected")
    .map(event => event.detail.sessionId);

globalThis.__hydrateResolvers[1].resolve();
await flushMicrotasks();
const selectedAfterB = globalThis.__documentDispatches
    .filter(event => event.type === "agent-teams-session-selected")
    .map(event => event.detail.sessionId);

globalThis.__hydrateResolvers[2].resolve();
await Promise.all([firstA, sessionB, secondA]);

const selectedEvents = globalThis.__documentDispatches
    .filter(event => event.type === "agent-teams-session-selected")
    .map(event => event.detail.sessionId);
const activatedEvents = globalThis.__documentDispatches
    .filter(event => event.type === "agent-teams-session-activated")
    .map(event => event.detail.sessionId);

console.log(JSON.stringify({
    currentSessionId: state.currentSessionId,
    fetchCalls: globalThis.__fetchCalls,
    hydrateCalls: globalThis.__hydrateCalls.map(call => call.sessionId),
    appliedRecords: globalThis.__appliedRecords.map(record => record.session_id),
    ensureSubagentCalls: globalThis.__ensureSubagentCalls,
    selectedAfterOldA,
    selectedAfterB,
    selectedEvents,
    activatedEvents,
    contextPreviewCalls: globalThis.__contextPreviewCalls,
    tokenUsageRefreshCalls: globalThis.__tokenUsageRefreshCalls,
    clearContextIndicatorOptions: globalThis.__clearContextIndicatorOptions,
    clearSessionTokenUsageOptions: globalThis.__clearSessionTokenUsageOptions,
}));
""".strip(),
    )

    assert payload["currentSessionId"] == "session-a"
    assert payload["fetchCalls"] == ["session-a", "session-b", "session-a"]
    assert payload["hydrateCalls"] == ["session-a", "session-b", "session-a"]
    assert payload["appliedRecords"] == ["session-a", "session-b", "session-a"]
    assert payload["selectedAfterOldA"] == []
    assert payload["selectedAfterB"] == []
    assert payload["selectedEvents"] == ["session-a"]
    assert payload["activatedEvents"] == ["session-a", "session-b", "session-a"]
    assert payload["ensureSubagentCalls"] == ["session-a"]
    assert payload["contextPreviewCalls"] == 1
    assert payload["tokenUsageRefreshCalls"] == 1
    assert payload["clearContextIndicatorOptions"] == [
        {"preserveDisplay": True},
        {"preserveDisplay": True},
        {"preserveDisplay": True},
    ]
    assert payload["clearSessionTokenUsageOptions"] == [
        {"preserveDisplay": True},
        {"preserveDisplay": True},
        {"preserveDisplay": True},
    ]


def test_select_session_retries_deferred_terminal_view_mark(
    tmp_path: Path,
) -> None:
    payload = _run_session_script(
        tmp_path=tmp_path,
        runner_source="""
import { selectSession } from "./session.mjs";

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};
globalThis.__terminalViewResponses = [
    { status: "deferred" },
    { status: "ok" },
];

const selection = selectSession("session-a");
await Promise.resolve();
globalThis.__hydrateResolvers[0].resolve();
await selection;
await new Promise(resolve => setTimeout(resolve, 300));

console.log(JSON.stringify({
    viewedTerminalRuns: globalThis.__viewedTerminalRuns,
}));
""".strip(),
    )

    assert payload["viewedTerminalRuns"] == ["session-a", "session-a"]


def test_select_session_marks_terminal_view_after_hydration(
    tmp_path: Path,
) -> None:
    payload = _run_session_script(
        tmp_path=tmp_path,
        runner_source="""
import { selectSession } from "./session.mjs";

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};

const selection = selectSession("session-a");
await Promise.resolve();
const viewedBeforeHydration = [...globalThis.__viewedTerminalRuns];
globalThis.__hydrateResolvers[0].resolve();
await selection;
await Promise.resolve();

console.log(JSON.stringify({
    viewedBeforeHydration,
    viewedAfterHydration: globalThis.__viewedTerminalRuns,
    ensureSubagentCalls: globalThis.__ensureSubagentCalls,
}));
""".strip(),
    )

    assert payload["viewedBeforeHydration"] == []
    assert payload["viewedAfterHydration"] == ["session-a"]
    assert payload["ensureSubagentCalls"] == ["session-a"]


def test_select_session_retries_overloaded_terminal_view_mark(
    tmp_path: Path,
) -> None:
    payload = _run_session_script(
        tmp_path=tmp_path,
        runner_source="""
import { selectSession } from "./session.mjs";

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};
globalThis.__terminalViewResponses = [
    { errorStatus: 503 },
    { status: "ok" },
];

const selection = selectSession("session-a");
await Promise.resolve();
globalThis.__hydrateResolvers[0].resolve();
await selection;
await new Promise(resolve => setTimeout(resolve, 300));

console.log(JSON.stringify({
    logs: globalThis.__logs,
    viewedTerminalRuns: globalThis.__viewedTerminalRuns,
}));
""".strip(),
    )

    logs = payload["logs"]
    assert isinstance(logs, list)
    assert not any("terminal_view_mark_failed" in str(log) for log in logs)
    assert payload["viewedTerminalRuns"] == ["session-a", "session-a"]


def test_select_session_declares_nonblocking_content_switch_loading() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    session_source = (
        repo_root / "frontend" / "dist" / "js" / "app" / "session.js"
    ).read_text(encoding="utf-8")
    interface_css = (
        repo_root / "frontend" / "dist" / "css" / "components" / "interface.css"
    ).read_text(encoding="utf-8")

    assert "beginSessionSwitchLoading(selectionToken, sessionId);" in session_source
    assert "finishSessionSwitchLoading(selectionToken, sessionId);" in session_source
    assert "isLatestSessionSelection(selectionToken, sessionId)" in session_source
    assert "SESSION_SWITCH_LOADING_DELAY_MS = 80" in session_source
    assert "session-switch-loading" in session_source
    assert "session.loading" in session_source
    assert ".chat-container.is-session-switching .chat-scroll" in interface_css
    assert ".session-switch-loading-spinner" in interface_css
    assert "@keyframes sessionSwitchContentReady" in interface_css


def _run_session_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "app" / "session.js"
    module_under_test_path = tmp_path / "session.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_agent_panel_path = tmp_path / "mockAgentPanel.mjs"
    mock_context_indicators_path = tmp_path / "mockContextIndicators.mjs"
    mock_message_renderer_path = tmp_path / "mockMessageRenderer.mjs"
    mock_session_token_usage_path = tmp_path / "mockSessionTokenUsage.mjs"
    mock_session_debug_badge_path = tmp_path / "mockSessionDebugBadge.mjs"
    mock_project_view_path = tmp_path / "mockProjectView.mjs"
    mock_new_session_draft_path = tmp_path / "mockNewSessionDraft.mjs"
    mock_sidebar_path = tmp_path / "mockSidebar.mjs"
    mock_subagent_sessions_path = tmp_path / "mockSubagentSessions.mjs"
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_recovery_path = tmp_path / "mockRecovery.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    mock_stream_path = tmp_path / "mockStream.mjs"
    mock_submission_path = tmp_path / "mockSubmission.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_prompt_path = tmp_path / "mockPrompt.mjs"

    mock_agent_panel_path.write_text(
        """
export function clearAllPanels() {
    globalThis.__clearAllPanelsCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_context_indicators_path.write_text(
        """
export function clearContextIndicators(options = {}) {
    globalThis.__clearContextIndicatorsCalls += 1;
    globalThis.__clearContextIndicatorOptions.push(options);
}

export function scheduleCoordinatorContextPreview() {
    globalThis.__contextPreviewCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_message_renderer_path.write_text(
        """
export function clearAllStreamState() {
    globalThis.__clearAllStreamStateCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_session_token_usage_path.write_text(
        """
export function clearSessionTokenUsage(options = {}) {
    globalThis.__clearSessionTokenUsageCalls += 1;
    globalThis.__clearSessionTokenUsageOptions.push(options);
}

export function scheduleSessionTokenUsageRefresh() {
    globalThis.__tokenUsageRefreshCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_session_debug_badge_path.write_text(
        """
export function syncSessionDebugBadge(sessionId) {
    globalThis.__sessionDebugBadgeCalls.push(sessionId);
}
""".strip(),
        encoding="utf-8",
    )
    mock_project_view_path.write_text(
        """
export function hideProjectView() {
    globalThis.__hideProjectViewCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_new_session_draft_path.write_text(
        """
export function clearNewSessionDraft() {
    globalThis.__clearNewSessionDraftCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_sidebar_path.write_text(
        """
export function setRoundsMode() {
    globalThis.__setRoundsModeCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_subagent_sessions_path.write_text(
        """
export function clearActiveSubagentSession() {
    globalThis.__clearActiveSubagentSessionCalls += 1;
}

export async function ensureSessionSubagents(sessionId) {
    globalThis.__ensureSubagentCalls.push(sessionId);
    return [];
}

export async function openSubagentSession() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    mock_api_path.write_text(
        """
export async function fetchSessionHistory(sessionId) {
    globalThis.__fetchCalls.push(sessionId);
    return { session_id: sessionId };
}

export async function markSessionTerminalRunViewed(sessionId) {
    globalThis.__viewedTerminalRuns.push(sessionId);
    if (Array.isArray(globalThis.__terminalViewResponses) && globalThis.__terminalViewResponses.length > 0) {
        const response = globalThis.__terminalViewResponses.shift();
        if (response?.errorStatus) {
            const error = new Error("busy");
            error.status = response.errorStatus;
            throw error;
        }
        return response;
    }
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )
    mock_recovery_path.write_text(
        """
export function clearSessionRecovery() {
    globalThis.__clearSessionRecoveryCalls += 1;
}

export async function hydrateSessionView(sessionId, options = {}) {
    const index = globalThis.__hydrateCalls.length;
    globalThis.__hydrateCalls.push({
        sessionId,
        includeRounds: options.includeRounds === true,
        index,
    });
    await new Promise(resolve => {
        globalThis.__hydrateResolvers.push({ sessionId, index, resolve });
    });
}

export function stopSessionContinuity(sessionId) {
    globalThis.__stopSessionContinuityCalls.push(sessionId);
}
""".strip(),
        encoding="utf-8",
    )
    mock_state_path.write_text(
        """
export const state = {
    currentSessionId: null,
    currentWorkspaceId: "",
    activeSubagentSession: null,
    isGenerating: false,
    activeEventSource: null,
    instanceRoleMap: {},
    roleInstanceMap: {},
    taskInstanceMap: {},
    taskStatusMap: {},
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    autoSwitchedSubagentInstances: {},
    pausedSubagent: null,
    sessionAgents: [],
    sessionTasks: [],
    selectedRoleId: null,
    agentViews: {},
    activeView: "main",
};

export function applyCurrentSessionRecord(record) {
    globalThis.__appliedRecords.push(record);
}

export function resetCurrentSessionTopology() {
    globalThis.__resetTopologyCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_stream_path.write_text(
        """
export function detachActiveStreamForSessionSwitch() {
    globalThis.__detachActiveStreamCalls += 1;
}

export function detachNormalModeSubagentStreamsForSessionSwitch(sessionId) {
    globalThis.__detachSubagentStreamCalls.push(sessionId);
}
""".strip(),
        encoding="utf-8",
    )
    mock_submission_path.write_text(
        """
export function detachForegroundSubmission() {
    globalThis.__detachForegroundSubmissionCalls += 1;
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    mock_dom_path.write_text(
        """
function createSessionItem(sessionId, workspaceId) {
    let className = "session-item";
    return {
        className,
        getAttribute(name) {
            if (name === "data-session-id") {
                return sessionId;
            }
            if (name === "data-workspace-id") {
                return workspaceId;
            }
            return null;
        },
        classList: {
            toggle(name, force) {
                const current = new Set(String(className || "").split(/\\s+/).filter(Boolean));
                const enabled = force ?? !current.has(name);
                if (enabled) {
                    current.add(name);
                } else {
                    current.delete(name);
                }
                className = Array.from(current).join(" ");
            },
        },
    };
}

const sessionItems = [
    createSessionItem("session-a", "workspace-a"),
    createSessionItem("session-b", "workspace-b"),
];

export const els = {
    chatContainer: {
        children: [],
        className: "",
        classList: {
            add(...names) {
                const current = new Set(String(els.chatContainer.className || "").split(/\\s+/).filter(Boolean));
                names.forEach(name => current.add(name));
                els.chatContainer.className = Array.from(current).join(" ");
            },
            remove(...names) {
                const current = new Set(String(els.chatContainer.className || "").split(/\\s+/).filter(Boolean));
                names.forEach(name => current.delete(name));
                els.chatContainer.className = Array.from(current).join(" ");
            },
        },
        querySelector(selector) {
            return selector === ".session-switch-loading"
                ? this.children.find(child => child.className === "session-switch-loading") || null
                : null;
        },
        appendChild(node) {
            this.children.push(node);
            return node;
        },
    },
    chatMessages: {
        innerHTML: "",
    },
};

export const documentMock = {
    querySelector(selector) {
        const match = String(selector || "").match(/data-session-id="([^"]+)"/);
        if (!match) {
            return null;
        }
        return sessionItems.find(item => item.getAttribute("data-session-id") === match[1]) || null;
    },
    querySelectorAll(selector) {
        return selector === ".session-item" ? sessionItems : [];
    },
    dispatchEvent(event) {
        globalThis.__documentDispatches.push({
            type: event.type,
            detail: event.detail,
        });
        return true;
    },
    createElement() {
        return {
            className: "",
            innerHTML: "",
            attributes: {},
            setAttribute(name, value) {
                this.attributes[name] = value;
            },
        };
    },
};
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
export function formatMessage(key, values = {}) {
    return `${key}:${values.session_id || ""}`;
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    mock_logger_path.write_text(
        """
export function sysLog(message) {
    globalThis.__logs.push(String(message));
}
""".strip(),
        encoding="utf-8",
    )
    mock_prompt_path.write_text(
        """
export function refreshSessionTopologyControls() {
    globalThis.__refreshSessionTopologyControlsCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../components/agentPanel.js", "./mockAgentPanel.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../components/sessionTokenUsage.js", "./mockSessionTokenUsage.mjs")
        .replace("../components/sessionDebugBadge.js", "./mockSessionDebugBadge.mjs")
        .replace("../components/projectView.js", "./mockProjectView.mjs")
        .replace("../components/newSessionDraft.js", "./mockNewSessionDraft.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../components/subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("./recovery.js", "./mockRecovery.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../core/submission.js", "./mockSubmission.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./prompt.js", "./mockPrompt.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
import {{ documentMock }} from "./mockDom.mjs";

globalThis.document = documentMock;
globalThis.__appliedRecords = [];
globalThis.__contextPreviewCalls = 0;
globalThis.__tokenUsageRefreshCalls = 0;
globalThis.__clearAllPanelsCalls = 0;
globalThis.__clearContextIndicatorsCalls = 0;
globalThis.__clearContextIndicatorOptions = [];
globalThis.__clearAllStreamStateCalls = 0;
globalThis.__clearSessionTokenUsageCalls = 0;
globalThis.__clearSessionTokenUsageOptions = [];
globalThis.__sessionDebugBadgeCalls = [];
globalThis.__hideProjectViewCalls = 0;
globalThis.__clearNewSessionDraftCalls = 0;
globalThis.__setRoundsModeCalls = 0;
globalThis.__clearActiveSubagentSessionCalls = 0;
globalThis.__ensureSubagentCalls = [];
globalThis.__fetchCalls = [];
globalThis.__viewedTerminalRuns = [];
globalThis.__hydrateCalls = [];
globalThis.__hydrateResolvers = [];
globalThis.__clearSessionRecoveryCalls = 0;
globalThis.__stopSessionContinuityCalls = [];
globalThis.__resetTopologyCalls = 0;
globalThis.__detachActiveStreamCalls = 0;
globalThis.__detachForegroundSubmissionCalls = 0;
globalThis.__detachSubagentStreamCalls = [];
globalThis.__documentDispatches = [];
globalThis.__refreshSessionTopologyControlsCalls = 0;
globalThis.__logs = [];

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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
