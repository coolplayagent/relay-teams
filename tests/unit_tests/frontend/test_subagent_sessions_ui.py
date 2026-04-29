# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_opening_subagent_session_hides_main_input_container(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [
        {
            role: "assistant",
            content: "ok",
        },
    ];
}

export async function fetchSessionSubagents() {
    return [];
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams() {
    globalThis.__syncNormalModeSubagentStreamsCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    globalThis.__clearAllPanelsCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto(body, options = {}) {
    globalThis.__renderCalls.push({
        sessionId: options.sessionId || null,
        instanceId: options.instanceId || null,
        runId: options.runId || null,
        overlayMode: options.overlayMode || null,
        status: options.status || null,
        runStatus: options.runStatus || null,
        runPhase: options.runPhase || null,
        replaceWhenReady: options.replaceWhenReady === true,
    });
    body.renderedMessages = [{ role: "assistant", text: "ok" }];
    return { messages: body.renderedMessages, streamOverlayEntry: null };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    globalThis.__hideRoundNavigatorCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
    activeView: "main",
    isGenerating: false,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
function createBodyElement() {
    return {
        innerHTML: "",
        renderedMessages: [],
    };
}

function createSectionElement() {
    const body = createBodyElement();
    return {
        className: "",
        dataset: {},
        _innerHTML: "",
        set innerHTML(value) {
            this._innerHTML = String(value);
        },
        get innerHTML() {
            return this._innerHTML;
        },
        querySelector(selector) {
            if (selector === ".subagent-session-body") {
                return body;
            }
            return null;
        },
    };
}

function createChatMessages() {
    return {
        innerHTML: "",
        children: [],
        appendChild(node) {
            this.children.push(node);
            return node;
        },
        querySelector(selector) {
            return this.children[0]?.querySelector?.(selector) || null;
        },
    };
}

export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: createChatMessages(),
};

globalThis.document = {
    createElement() {
        return createSectionElement();
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
const translations = {
    "subagent_session.read_only": "Read-only subagent session",
    "subagent.task_prompt": "Task prompt",
    "subagent_session.empty": "No messages",
    "subagent_session.load_failed": "Load failed",
};

export function t(key) {
    return translations[key] || key;
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

    runner_path.write_text(
        """
globalThis.__renderCalls = [];
globalThis.__clearAllPanelsCalls = 0;
globalThis.__hideRoundNavigatorCalls = 0;
globalThis.__syncNormalModeSubagentStreamsCalls = 0;

const { els } = await import("./mockDom.mjs");
const { state } = await import("./mockState.mjs");
const {
    clearActiveSubagentSession,
    openSubagentSession,
} = await import("./subagentSessions.mjs");

await openSubagentSession("session-1", {
    sessionId: "session-1",
    instanceId: "inst-sub-1",
    roleId: "Explorer",
    runId: "subagent_run_1",
    title: "Explore history",
    status: "running",
});

const hiddenWhileOpen = els.inputContainer.style.display || "";
const hintWhileOpen = els.promptInputHint.textContent;
const sendDisabledWhileOpen = els.sendBtn.disabled;
const activeViewWhileOpen = state.activeView;

clearActiveSubagentSession();

console.log(JSON.stringify({
    hiddenWhileOpen,
    hintWhileOpen,
    sendDisabledWhileOpen,
    activeViewWhileOpen,
    hiddenAfterClear: els.inputContainer.style.display || "",
    hintAfterClear: els.promptInputHint.textContent,
    sendDisabledAfterClear: els.sendBtn.disabled,
    activeViewAfterClear: state.activeView,
    renderCalls: globalThis.__renderCalls,
    clearAllPanelsCalls: globalThis.__clearAllPanelsCalls,
    hideRoundNavigatorCalls: globalThis.__hideRoundNavigatorCalls,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["hiddenWhileOpen"] == "none"
    assert payload["hintWhileOpen"] == "Read-only subagent session"
    assert payload["sendDisabledWhileOpen"] is True
    assert payload["activeViewWhileOpen"] == "subagent-session"
    assert payload["hiddenAfterClear"] == ""
    assert payload["hintAfterClear"] == ""
    assert payload["sendDisabledAfterClear"] is False
    assert payload["activeViewAfterClear"] == "main"
    assert payload["renderCalls"] == [
        {
            "sessionId": "session-1",
            "instanceId": "inst-sub-1",
            "runId": "subagent_run_1",
            "overlayMode": "separate",
            "status": "running",
            "runStatus": "running",
            "runPhase": None,
            "replaceWhenReady": True,
        }
    ]
    assert payload["clearAllPanelsCalls"] == 1
    assert payload["hideRoundNavigatorCalls"] == 2


def test_return_to_main_session_clears_subagent_view_before_hydration(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_return_main.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionSubagents() {
    return [];
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
import { els } from "./mockDom.mjs";

export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView(sessionId, options = {}) {
    els.chatMessages.innerHTML = "<div class='subagent-main-session-loading'></div>";
    document.dispatchEvent(new CustomEvent("agent-teams-subagent-session-cleared", {
        detail: { sessionId },
    }));
    globalThis.__hydrateCalls.push({
        sessionId,
        includeRounds: true,
        quiet: options.quiet === true,
    });
    await new Promise(resolve => {
        globalThis.__hydrateResolvers.push({ resolve });
    });
    document.dispatchEvent(new CustomEvent("agent-teams-session-activated", {
        detail: { sessionId },
    }));
    document.dispatchEvent(new CustomEvent("agent-teams-session-selected", {
        detail: { sessionId },
    }));
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto() {
    return { messages: [], streamOverlayEntry: null };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: {
        sessionId: "session-1",
        instanceId: "inst-sub-1",
        roleId: "Explorer",
    },
    activeView: "subagent-session",
    isGenerating: false,
    activeAgentRoleId: "Explorer",
    activeAgentInstanceId: "inst-sub-1",
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    inputContainer: { style: { display: "none" } },
    promptInput: { disabled: true },
    sendBtn: { disabled: true },
    promptInputHint: { textContent: "Read-only subagent session" },
    chatMessages: { innerHTML: "<section class='subagent-session-view'></section>" },
};

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};

globalThis.document = {
    dispatchEvent(event) {
        globalThis.__events.push({ type: event.type, detail: event.detail });
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
const translations = {
    "session.loading": "Loading conversation...",
    "subagent_session.load_failed": "Failed to load subagent history.",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog(message) {
    globalThis.__logs.push(String(message));
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__events = [];
globalThis.__hydrateCalls = [];
globalThis.__hydrateResolvers = [];
globalThis.__logs = [];

const { els } = await import("./mockDom.mjs");
const { state } = await import("./mockState.mjs");
const { returnToMainSessionView } = await import("./subagentSessions.mjs");

const returning = returnToMainSessionView();
await Promise.resolve();

const immediate = {
    activeView: state.activeView,
    activeSubagentSession: state.activeSubagentSession,
    inputDisplay: els.inputContainer.style.display || "",
    promptDisabled: els.promptInput.disabled,
    sendDisabled: els.sendBtn.disabled,
    hint: els.promptInputHint.textContent,
    loadingVisible: els.chatMessages.innerHTML.includes("subagent-main-session-loading"),
    eventTypes: globalThis.__events.map(event => event.type),
};

globalThis.__hydrateResolvers[0].resolve();
await returning;

console.log(JSON.stringify({
    immediate,
    finalEventTypes: globalThis.__events.map(event => event.type),
    hydrateCalls: globalThis.__hydrateCalls,
    logs: globalThis.__logs,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["immediate"] == {
        "activeView": "main",
        "activeSubagentSession": None,
        "inputDisplay": "",
        "promptDisabled": False,
        "sendDisabled": False,
        "hint": "",
        "loadingVisible": True,
        "eventTypes": [
            "agent-teams-subagent-session-cleared",
        ],
    }
    assert payload["finalEventTypes"] == [
        "agent-teams-subagent-session-cleared",
        "agent-teams-session-activated",
        "agent-teams-session-selected",
    ]
    assert payload["hydrateCalls"] == [
        {
            "sessionId": "session-1",
            "includeRounds": True,
            "quiet": True,
        }
    ]
    assert payload["logs"] == []


def test_return_to_main_session_ignores_stale_hydration_after_subagent_reentry(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_return_stale.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        "export async function fetchSessionSubagents() { return []; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        "export function syncNormalModeSubagentStreams() { return undefined; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
import { state } from "./mockState.mjs";

export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView(sessionId) {
    document.dispatchEvent(new CustomEvent("agent-teams-subagent-session-cleared", {
        detail: { sessionId },
    }));
    await new Promise(resolve => {
        globalThis.__hydrateResolvers.push({ resolve });
    });
    if (!state.activeSubagentSession) {
        document.dispatchEvent(new CustomEvent("agent-teams-session-selected", {
            detail: { sessionId },
        }));
    }
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        "export function clearAllPanels() { return undefined; }\n", encoding="utf-8"
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        "export async function renderInstanceHistoryInto() { return { messages: [], streamOverlayEntry: null }; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        "export function hideRoundNavigator() { return undefined; }\n", encoding="utf-8"
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: { sessionId: "session-1", instanceId: "inst-sub-1" },
    activeView: "subagent-session",
    isGenerating: false,
    activeAgentRoleId: "Explorer",
    activeAgentInstanceId: "inst-sub-1",
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: { innerHTML: "" },
};

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};

globalThis.document = {
    dispatchEvent(event) {
        globalThis.__events.push({ type: event.type, detail: event.detail });
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        "export function t(key) { return key; }\n", encoding="utf-8"
    )
    (tmp_path / "mockLogger.mjs").write_text(
        "export function sysLog(message) { globalThis.__logs.push(String(message)); }\n",
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__events = [];
globalThis.__hydrateResolvers = [];
globalThis.__logs = [];

const { state } = await import("./mockState.mjs");
const { returnToMainSessionView } = await import("./subagentSessions.mjs");

const returning = returnToMainSessionView();
await Promise.resolve();
state.activeSubagentSession = { sessionId: "session-1", instanceId: "inst-sub-2" };
state.activeView = "subagent-session";
globalThis.__hydrateResolvers[0].resolve();
await returning;

console.log(JSON.stringify({
    finalActiveSubagentSession: state.activeSubagentSession,
    eventTypes: globalThis.__events.map(event => event.type),
    logs: globalThis.__logs,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["finalActiveSubagentSession"] == {
        "sessionId": "session-1",
        "instanceId": "inst-sub-2",
    }
    assert payload["eventTypes"] == [
        "agent-teams-subagent-session-cleared",
    ]
    assert payload["logs"] == []


def test_ensure_session_subagents_syncs_running_streams_for_current_session(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_sync.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [];
}

export async function fetchSessionSubagents() {
    return [
        {
            instance_id: "inst-sub-1",
            role_id: "Explorer",
            run_id: "subagent_run_1",
            title: "Explore history",
            status: "running",
            run_status: "running",
            run_phase: "running",
            last_event_id: 9,
            checkpoint_event_id: 7,
            stream_connected: false,
            conversation_id: "conv-1",
        },
    ];
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams(sessionId, records) {
    globalThis.__syncCalls.push({
        sessionId,
        runId: Array.isArray(records) ? records[0]?.runId || null : null,
        runStatus: Array.isArray(records) ? records[0]?.runStatus || null : null,
        lastEventId: Array.isArray(records) ? records[0]?.lastEventId || 0 : 0,
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto() {
    return { messages: [], streamOverlayEntry: null };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
    activeView: "main",
    isGenerating: false,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: null,
};

globalThis.document = {
    dispatchEvent() {
        return undefined;
    },
};
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
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__syncCalls = [];

const { ensureSessionSubagents } = await import("./subagentSessions.mjs");

const rows = await ensureSessionSubagents("session-1", { force: true });

console.log(JSON.stringify({
    rowCount: Array.isArray(rows) ? rows.length : 0,
    syncCalls: globalThis.__syncCalls,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["rowCount"] == 1
    assert payload["syncCalls"] == [
        {
            "sessionId": "session-1",
            "runId": "subagent_run_1",
            "runStatus": "running",
            "lastEventId": 9,
        }
    ]


def test_ensure_session_subagents_limits_parallel_backend_loads(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_concurrency.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [];
}

export async function fetchSessionSubagents(sessionId) {
    globalThis.__activeLoads += 1;
    globalThis.__maxActiveLoads = Math.max(
        globalThis.__maxActiveLoads,
        globalThis.__activeLoads,
    );
    globalThis.__loadCalls.push(sessionId);
    return await new Promise(resolve => {
        globalThis.__loadResolvers.push(() => {
            globalThis.__activeLoads -= 1;
            resolve([]);
        });
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function abortMainSessionRestore() {
    return undefined;
}

export async function restoreMainSessionView() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto() {
    return { messages: [], streamOverlayEntry: null };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
    activeView: "main",
    isGenerating: false,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: null,
};

globalThis.document = {
    dispatchEvent() {
        return undefined;
    },
};
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
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__activeLoads = 0;
globalThis.__maxActiveLoads = 0;
globalThis.__loadCalls = [];
globalThis.__loadResolvers = [];

const { ensureSessionSubagents } = await import("./subagentSessions.mjs");

const promises = Array.from({ length: 5 }, (_, index) => (
    ensureSessionSubagents(`session-${index}`, { force: true })
));
await Promise.resolve();
const callsAfterStart = [...globalThis.__loadCalls];
const activeAfterStart = globalThis.__activeLoads;

while (globalThis.__loadResolvers.length > 0) {
    const resolver = globalThis.__loadResolvers.shift();
    resolver();
    await Promise.resolve();
}
await Promise.all(promises);

console.log(JSON.stringify({
    activeAfterStart,
    callsAfterStart,
    loadCalls: globalThis.__loadCalls,
    maxActiveLoads: globalThis.__maxActiveLoads,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["activeAfterStart"] == 2
    assert payload["callsAfterStart"] == ["session-0", "session-1"]
    assert payload["loadCalls"] == [
        "session-0",
        "session-1",
        "session-2",
        "session-3",
        "session-4",
    ]
    assert payload["maxActiveLoads"] == 2


def test_subagent_status_update_emits_sidebar_refresh_event(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_status_events.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [];
}

export async function fetchSessionSubagents() {
    return [];
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams(sessionId, records) {
    globalThis.__syncCalls.push({
        sessionId,
        statuses: Array.isArray(records) ? records.map(record => record.runStatus || record.status || "") : [],
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto() {
    return { messages: [], streamOverlayEntry: null };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
    activeView: "main",
    isGenerating: false,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: null,
};

globalThis.document = {
    dispatchEvent(event) {
        globalThis.__events.push({
            type: event.type,
            detail: event.detail || null,
        });
        return true;
    },
};
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
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__events = [];
globalThis.__syncCalls = [];
globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || null;
    }
};

const {
    replaceSessionSubagents,
    updateNormalModeSubagentSessionStatus,
} = await import("./subagentSessions.mjs");

replaceSessionSubagents("session-1", [
    {
        instance_id: "inst-sub-1",
        role_id: "Explorer",
        run_id: "subagent_run_1",
        status: "running",
        run_status: "running",
    },
], { emitChange: false });
globalThis.__events = [];

replaceSessionSubagents("session-1", [
    {
        instance_id: "inst-sub-1",
        role_id: "Explorer",
        run_id: "subagent_run_1",
        status: "completed",
        run_status: "completed",
        run_phase: "finished",
        last_event_id: 12,
    },
], { emitChange: true });
const replaceEvents = [...globalThis.__events];
globalThis.__events = [];

updateNormalModeSubagentSessionStatus("session-1", "inst-sub-1", "failed");
updateNormalModeSubagentSessionStatus("session-1", "inst-sub-1", "failed");

console.log(JSON.stringify({
    replaceEvents,
    events: globalThis.__events,
    syncCalls: globalThis.__syncCalls,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["replaceEvents"] == [
        {
            "type": "agent-teams-subagent-session-status-changed",
            "detail": {
                "sessionId": "session-1",
                "instanceId": "",
                "status": "updated",
            },
        },
    ]
    assert payload["events"] == [
        {
            "type": "agent-teams-subagent-session-status-changed",
            "detail": {
                "sessionId": "session-1",
                "instanceId": "inst-sub-1",
                "status": "failed",
            },
        },
    ]
    assert payload["syncCalls"][-1] == {
        "sessionId": "session-1",
        "statuses": ["failed"],
    }


def test_background_task_event_records_normal_mode_subagent_immediately(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_background_task.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [];
}

export async function fetchSessionSubagents() {
    globalThis.__subagentFetches += 1;
    return [];
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams(sessionId, records) {
    globalThis.__syncCalls.push({
        sessionId,
        records: Array.isArray(records) ? records.map(record => ({
            instanceId: record.instanceId,
            roleId: record.roleId,
            runId: record.runId,
            status: record.status,
            runStatus: record.runStatus,
            title: record.title,
        })) : [],
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto() {
    return { messages: [], streamOverlayEntry: null };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
    activeView: "main",
    isGenerating: false,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: null,
};

globalThis.document = {
    dispatchEvent(event) {
        globalThis.__events.push({
            type: event.type,
            detail: event.detail || null,
        });
        return true;
    },
};
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
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__events = [];
globalThis.__syncCalls = [];
globalThis.__subagentFetches = 0;
globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || null;
    }
};

const {
    getSessionSubagentSessions,
    rememberNormalModeSubagentFromBackgroundTask,
} = await import("./subagentSessions.mjs");

const remembered = rememberNormalModeSubagentFromBackgroundTask(
    "session-1",
    {
        kind: "subagent",
        subagent_instance_id: "Explorer-abc123",
        subagent_role_id: "Explorer",
        subagent_run_id: "subagent_run_abc123",
        title: "Explore command implementation",
        status: "running",
        updated_at: "2026-04-28T11:00:00Z",
    },
    "background_task_started",
);
const failedRemembered = rememberNormalModeSubagentFromBackgroundTask(
    "session-1",
    {
        kind: "subagent",
        subagent_instance_id: "Explorer-abc123",
        subagent_role_id: "Explorer",
        subagent_run_id: "subagent_run_abc123",
        title: "Explore command implementation",
        status: "failed",
        updated_at: "2026-04-28T11:01:00Z",
    },
    "background_task_completed",
);
await new Promise(resolve => setTimeout(resolve, 25));

console.log(JSON.stringify({
    remembered,
    failedRemembered,
    rows: getSessionSubagentSessions("session-1"),
    events: globalThis.__events,
    syncCalls: globalThis.__syncCalls,
    subagentFetches: globalThis.__subagentFetches,
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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["remembered"] is True
    assert payload["failedRemembered"] is True
    assert payload["subagentFetches"] == 0
    assert payload["rows"] == [
        {
            "sessionId": "session-1",
            "instanceId": "Explorer-abc123",
            "roleId": "Explorer",
            "runId": "subagent_run_abc123",
            "title": "Explore command implementation",
            "status": "failed",
            "runStatus": "failed",
            "runPhase": "",
            "lastEventId": 0,
            "checkpointEventId": 0,
            "streamConnected": False,
            "createdAt": "",
            "updatedAt": "2026-04-28T11:01:00Z",
            "conversationId": "",
        }
    ]
    assert payload["events"]
    assert payload["events"][-1]["detail"]["sessionId"] == "session-1"
    assert payload["syncCalls"][-1]["records"][0]["runId"] == "subagent_run_abc123"
    assert payload["syncCalls"][-1]["records"][0]["status"] == "failed"


def test_terminal_settle_retries_until_history_is_safe(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentSessions.js"
    )
    module_under_test_path = tmp_path / "subagentSessions.mjs"
    runner_path = tmp_path / "runner_terminal_settle.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../app/sessionView.js", "./mockRecovery.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./agentPanel/history.js", "./mockAgentPanelHistory.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchAgentMessages() {
    return [];
}

export async function fetchSessionSubagents() {
    return [];
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockStream.mjs").write_text(
        """
export function syncNormalModeSubagentStreams() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function abortMainSessionRestore() {
    globalThis.__abortMainSessionRestoreCalls = (globalThis.__abortMainSessionRestoreCalls || 0) + 1;
}

export async function restoreMainSessionView() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanelHistory.mjs").write_text(
        """
export async function renderInstanceHistoryInto(_body, options = {}) {
    globalThis.__renderCalls.push({
        requireToolBoundary: options.requireToolBoundary === true,
        replaceWhenReady: options.replaceWhenReady === true,
        loadingAtRender: String(_body?.owner?.className || "").includes("is-loading"),
    });
    if (
        options.requireToolBoundary === true
        && globalThis.__renderCalls.filter(item => item.requireToolBoundary === true).length === 1
    ) {
        return { deferred: true };
    }
    return { deferred: false };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
    activeView: "main",
    isGenerating: false,
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
};

export function getRoleDisplayName(roleId, { fallback } = {}) {
    return String(roleId || fallback || "Agent");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
function createBodyElement() {
    return {
        innerHTML: "",
        dataset: {},
    };
}

function createSectionElement() {
    const loading = { hidden: true };
    const section = {
        className: "",
        dataset: {},
        _innerHTML: "",
        set innerHTML(value) {
            this._innerHTML = String(value);
        },
        get innerHTML() {
            return this._innerHTML;
        },
        querySelector(selector) {
            if (selector === ".subagent-session-title") return { textContent: "" };
            if (selector === ".subagent-session-badge") return { className: "", textContent: "" };
            if (selector === ".subagent-session-meta") return { textContent: "" };
            if (selector === ".subagent-session-loading") return loading;
            if (selector === ".subagent-session-body") return body;
            return null;
        },
    };
    const body = createBodyElement();
    body.owner = section;
    return section;
}

function createChatMessages() {
    return {
        innerHTML: "",
        children: [],
        appendChild(node) {
            this.children.push(node);
            return node;
        },
        querySelector(selector) {
            if (selector === ".subagent-session-view") {
                return this.children[0] || null;
            }
            return this.children[0]?.querySelector?.(selector) || null;
        },
    };
}

export const els = {
    inputContainer: { style: {} },
    promptInput: { disabled: false },
    sendBtn: { disabled: false },
    promptInputHint: { textContent: "" },
    chatMessages: createChatMessages(),
};

globalThis.document = {
    createElement() {
        return createSectionElement();
    },
    dispatchEvent() {
        return undefined;
    },
};
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
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__renderCalls = [];

const { els } = await import("./mockDom.mjs");
const { openSubagentSession, settleActiveSubagentSessionAfterTerminal } = await import("./subagentSessions.mjs");

await openSubagentSession("session-1", {
    sessionId: "session-1",
    instanceId: "inst-sub-1",
    roleId: "Explorer",
    runId: "subagent_run_1",
    title: "Explore history",
    status: "completed",
});

settleActiveSubagentSessionAfterTerminal("inst-sub-1");
await new Promise(resolve => setTimeout(resolve, 180));

console.log(JSON.stringify({
    renderCalls: globalThis.__renderCalls,
    finalWrapperClassName: String(els.chatMessages.children[0]?.className || ""),
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
        timeout=3,
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
            "requireToolBoundary": False,
            "replaceWhenReady": True,
            "loadingAtRender": True,
        },
        {
            "requireToolBoundary": True,
            "replaceWhenReady": True,
            "loadingAtRender": False,
        },
        {
            "requireToolBoundary": True,
            "replaceWhenReady": True,
            "loadingAtRender": False,
        },
    ]
    assert payload["finalWrapperClassName"] == "subagent-session-view"
