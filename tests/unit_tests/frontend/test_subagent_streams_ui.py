from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_normal_mode_subagent_streams_attach_route_and_detach(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "stream.js"
    module_under_test_path = tmp_path / "stream.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./api.js", "./mockApi.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../app/prompt.js", "./mockPrompt.mjs")
        .replace("../components/subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/backendStatus.js", "./mockBackendStatus.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./eventRouter.js", "./mockEventRouter.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("./state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionRecovery() {
    return {};
}

export async function fetchSessionSubagents() {
    return [];
}

export async function fetchSessions() {
    return [];
}

export async function sendUserPrompt() {
    throw new Error("not used");
}

export async function stopRun() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPrompt.mjs").write_text(
        """
export function refreshSessionTopologyControls() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function replaceSessionSubagents() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSidebar.mjs").write_text(
        """
export function scheduleSessionsRefresh() {
    globalThis.__scheduleSessionsRefreshCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    sendBtn: { disabled: false },
    promptInput: { disabled: false, focus() {} },
    yoloToggle: { disabled: false },
    thinkingModeToggle: { disabled: false },
    thinkingEffortSelect: { disabled: false },
    stopBtn: { style: {}, disabled: false },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockBackendStatus.mjs").write_text(
        """
export function markBackendOnline() {
    globalThis.__markBackendOnlineCalls += 1;
}

export async function refreshBackendStatus() {
    globalThis.__refreshBackendStatusCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { error: String(error?.message || error || ''), ...extra };
}

export function logError() {
    return undefined;
}

export function logInfo() {
    return undefined;
}

export function logWarn() {
    return undefined;
}

export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockEventRouter.mjs").write_text(
        """
export function routeEvent(evType, payload, eventMeta) {
    globalThis.__routeEventCalls.push({ evType, payload, eventMeta });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    activeSubagentSession: null,
    activeEventSource: null,
    activeRunId: null,
    isGenerating: false,
    runPrimaryRoleMap: {},
};

export function getPrimaryRoleId() {
    return "MainAgent";
}

export function getPrimaryRoleLabel() {
    return "Main Agent";
}

export function getRunPrimaryRoleId() {
    return "MainAgent";
}

export function getRunPrimaryRoleLabel() {
    return "Main Agent";
}

export function setRunPrimaryRole() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__routeEventCalls = [];
globalThis.__scheduleSessionsRefreshCalls = 0;
globalThis.__markBackendOnlineCalls = 0;
globalThis.__refreshBackendStatusCalls = 0;
globalThis.__eventSources = [];

class MockEventSource {
    constructor(url) {
        this.url = url;
        this.closed = false;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        globalThis.__eventSources.push(this);
    }

    close() {
        this.closed = true;
    }
}

globalThis.EventSource = MockEventSource;

const {
    detachNormalModeSubagentStreamsForSessionSwitch,
    syncNormalModeSubagentStreams,
} = await import("./stream.mjs");

syncNormalModeSubagentStreams("session-1", [
    {
        instance_id: "inst-sub-1",
        role_id: "Explorer",
        run_id: "subagent_run_1",
        status: "running",
        run_status: "running",
        last_event_id: 9,
        checkpoint_event_id: 4,
    },
]);

const [eventSource] = globalThis.__eventSources;
syncNormalModeSubagentStreams("session-2", [
    {
        instance_id: "inst-sub-2",
        role_id: "Explorer",
        run_id: "subagent_run_2",
        status: "running",
        run_status: "running",
        last_event_id: 3,
    },
]);
const closedAfterNonCurrentSync = eventSource?.closed === true;
eventSource.onmessage({
    data: JSON.stringify({
        event_id: 10,
        event_type: "text_delta",
        payload_json: JSON.stringify({
            instance_id: "inst-sub-1",
            role_id: "Explorer",
            text: "ok",
        }),
        run_id: "subagent_run_1",
        trace_id: "subagent_run_1",
    }),
});
eventSource.onmessage({
    data: JSON.stringify({
        event_id: 11,
        event_type: "run_completed",
        payload_json: JSON.stringify({
            instance_id: "inst-sub-1",
            role_id: "Explorer",
        }),
        run_id: "subagent_run_1",
        trace_id: "subagent_run_1",
    }),
});

detachNormalModeSubagentStreamsForSessionSwitch("session-1");

console.log(JSON.stringify({
    eventSourceUrl: eventSource?.url || null,
    closedAfterNonCurrentSync,
    eventSourceClosed: eventSource?.closed === true,
    routeEventCalls: globalThis.__routeEventCalls,
    scheduleSessionsRefreshCalls: globalThis.__scheduleSessionsRefreshCalls,
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

    assert (
        payload["eventSourceUrl"]
        == "/api/sessions/session-1/subagents/events?after_event_id=9"
    )
    assert payload["closedAfterNonCurrentSync"] is False
    assert payload["eventSourceClosed"] is True
    assert payload["routeEventCalls"] == [
        {
            "evType": "text_delta",
            "payload": {
                "instance_id": "inst-sub-1",
                "role_id": "Explorer",
                "text": "ok",
            },
            "eventMeta": {
                "event_id": 10,
                "event_type": "text_delta",
                "payload_json": '{"instance_id":"inst-sub-1","role_id":"Explorer","text":"ok"}',
                "run_id": "subagent_run_1",
                "trace_id": "subagent_run_1",
            },
        },
        {
            "evType": "run_completed",
            "payload": {
                "instance_id": "inst-sub-1",
                "role_id": "Explorer",
            },
            "eventMeta": {
                "event_id": 11,
                "event_type": "run_completed",
                "payload_json": '{"instance_id":"inst-sub-1","role_id":"Explorer"}',
                "run_id": "subagent_run_1",
                "trace_id": "subagent_run_1",
            },
        },
    ]
    assert payload["scheduleSessionsRefreshCalls"] == 0


def test_active_parent_run_keeps_normal_subagent_discovery_polling(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "stream.js"
    module_under_test_path = tmp_path / "stream.mjs"
    runner_path = tmp_path / "runner_parent_discovery.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./api.js", "./mockApi.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../app/prompt.js", "./mockPrompt.mjs")
        .replace("../components/subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/backendStatus.js", "./mockBackendStatus.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./eventRouter.js", "./mockEventRouter.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("./state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionRecovery() {
    return {};
}

export async function fetchSessionSubagents(sessionId) {
    globalThis.__fetchSubagentCalls.push(sessionId);
    return [];
}

export async function fetchSessions() {
    globalThis.__fetchSessionsCalls += 1;
    return [];
}

export async function sendUserPrompt() {
    throw new Error("not used");
}

export async function stopRun() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPrompt.mjs").write_text(
        """
export function refreshSessionTopologyControls() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function replaceSessionSubagents(sessionId, payload) {
    globalThis.__replaceSessionSubagentsCalls.push({
        sessionId,
        rowCount: Array.isArray(payload) ? payload.length : 0,
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSidebar.mjs").write_text(
        """
export function scheduleSessionsRefresh() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    sendBtn: { disabled: false },
    promptInput: { disabled: false, focus() {} },
    yoloToggle: { disabled: false },
    thinkingModeToggle: { disabled: false },
    thinkingEffortSelect: { disabled: false },
    stopBtn: { style: {}, disabled: false },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockBackendStatus.mjs").write_text(
        """
export function markBackendOnline() {
    return undefined;
}

export async function refreshBackendStatus() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { error: String(error?.message || error || ''), ...extra };
}

export function logError() {
    return undefined;
}

export function logInfo() {
    return undefined;
}

export function logWarn() {
    return undefined;
}

export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockEventRouter.mjs").write_text(
        """
export function routeEvent() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    activeSubagentSession: null,
    activeEventSource: null,
    activeRunId: null,
    isGenerating: false,
    runPrimaryRoleMap: {},
};

export function getPrimaryRoleId() {
    return "MainAgent";
}

export function getPrimaryRoleLabel() {
    return "Main Agent";
}

export function getRunPrimaryRoleId() {
    return "MainAgent";
}

export function getRunPrimaryRoleLabel() {
    return "Main Agent";
}

export function setRunPrimaryRole() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__eventSources = [];
globalThis.__fetchSubagentCalls = [];
globalThis.__fetchSessionsCalls = 0;
globalThis.__replaceSessionSubagentsCalls = [];
globalThis.__timers = [];
globalThis.setTimeout = (callback, delay) => {
    const timer = {
        callback,
        delay,
        cleared: false,
        unref() {},
    };
    globalThis.__timers.push(timer);
    return timer;
};
globalThis.clearTimeout = timer => {
    if (timer) {
        timer.cleared = true;
    }
};

class MockEventSource {
    constructor(url) {
        this.url = url;
        this.closed = false;
        this.onmessage = null;
        this.onerror = null;
        globalThis.__eventSources.push(this);
    }

    close() {
        this.closed = true;
    }
}

globalThis.EventSource = MockEventSource;

const { attachRunStream } = await import("./stream.mjs");

attachRunStream("run-1", "session-1", null, {
    reason: "test",
    makeUiBusy: true,
});

const timersAfterAttach = [...globalThis.__timers];
for (const timer of timersAfterAttach) {
    if (!timer.cleared) {
        timer.callback();
    }
}
await Promise.resolve();
await Promise.resolve();
await Promise.resolve();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    eventSourceUrls: globalThis.__eventSources.map(source => source.url),
    scheduledDelays: timersAfterAttach.map(timer => timer.delay),
    fetchSubagentCalls: globalThis.__fetchSubagentCalls,
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

    assert payload["eventSourceUrls"] == ["/api/runs/run-1/events"]
    assert 2500 in payload["scheduledDelays"]
    assert payload["fetchSubagentCalls"] == ["session-1"]


def test_current_session_background_stream_routes_events_and_deduplicates_attach(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "stream.js"
    module_under_test_path = tmp_path / "stream.mjs"
    runner_path = tmp_path / "runner_background_current.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./api.js", "./mockApi.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../app/prompt.js", "./mockPrompt.mjs")
        .replace("../app/recovery.js", "./mockRecovery.mjs")
        .replace("../components/subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/backendStatus.js", "./mockBackendStatus.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./eventRouter.js", "./mockEventRouter.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("./state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionRecovery(sessionId) {
    globalThis.__fetchRecoveryCalls.push(sessionId);
    await new Promise(resolve => setTimeout(resolve, 0));
    return {
        active_run: {
            run_id: "run-1",
            status: "running",
            last_event_id: 1,
            primary_role_id: "MainAgent",
        },
    };
}

export async function fetchSessionSubagents() {
    return [];
}

export async function fetchSessions() {
    return [];
}

export async function sendUserPrompt() {
    throw new Error("not used");
}

export async function stopRun() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPrompt.mjs").write_text(
        """
export function refreshSessionTopologyControls() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export async function hydrateSessionView(sessionId, options = {}) {
    globalThis.__hydrateCalls.push({
        sessionId,
        includeRounds: options.includeRounds === true,
        roundsScrollPolicy: options.roundsScrollPolicy || "",
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function replaceSessionSubagents() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSidebar.mjs").write_text(
        """
export function scheduleSessionsRefresh() {
    globalThis.__scheduleSessionsRefreshCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    sendBtn: { disabled: false },
    promptInput: { disabled: false, focus() {} },
    yoloToggle: { disabled: false },
    thinkingModeToggle: { disabled: false },
    thinkingEffortSelect: { disabled: false },
    stopBtn: { style: {}, disabled: false },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockBackendStatus.mjs").write_text(
        """
export function markBackendOnline() {
    return undefined;
}

export async function refreshBackendStatus() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { error: String(error?.message || error || ''), ...extra };
}

export function logError() {
    return undefined;
}

export function logInfo() {
    return undefined;
}

export function logWarn() {
    return undefined;
}

export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockEventRouter.mjs").write_text(
        """
export function routeEvent(evType, payload, eventMeta) {
    globalThis.__routeEventCalls.push({ evType, payload, eventMeta });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() {
    return undefined;
}

export function applyStreamOverlayEvent(evType, payload, eventMeta) {
    globalThis.__overlayCalls.push({ evType, payload, eventMeta });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    activeSubagentSession: null,
    activeEventSource: null,
    activeRunId: null,
    isGenerating: false,
    runPrimaryRoleMap: {},
};

export function getPrimaryRoleId() {
    return "MainAgent";
}

export function getPrimaryRoleLabel() {
    return "Main Agent";
}

export function getRunPrimaryRoleId() {
    return "MainAgent";
}

export function getRunPrimaryRoleLabel() {
    return "Main Agent";
}

export function setRunPrimaryRole(runId, roleId) {
    state.runPrimaryRoleMap[runId] = roleId;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__fetchRecoveryCalls = [];
globalThis.__routeEventCalls = [];
globalThis.__overlayCalls = [];
globalThis.__hydrateCalls = [];
globalThis.__scheduleSessionsRefreshCalls = 0;
globalThis.__eventSources = [];

class MockEventSource {
    constructor(url) {
        this.url = url;
        this.closed = false;
        this.onmessage = null;
        this.onerror = null;
        globalThis.__eventSources.push(this);
    }

    close() {
        this.closed = true;
    }
}

globalThis.EventSource = MockEventSource;

const { syncBackgroundStreamsForSessions } = await import("./stream.mjs");

const runningRecord = {
    session_id: "session-1",
    active_run_id: "run-1",
    active_run_status: "running",
    updated_at: "2026-04-27T00:00:00.000Z",
};
syncBackgroundStreamsForSessions([runningRecord]);
syncBackgroundStreamsForSessions([runningRecord]);
await new Promise(resolve => setTimeout(resolve, 20));

const eventSource = globalThis.__eventSources[0];
eventSource.onmessage({
    data: JSON.stringify({
        event_id: 2,
        event_type: "todo_updated",
        payload_json: JSON.stringify({
            run_id: "run-1",
            session_id: "session-1",
            items: [{ content: "Persist todo", status: "completed" }],
            version: 1,
        }),
        run_id: "run-1",
        trace_id: "run-1",
    }),
});
eventSource.onmessage({
    data: JSON.stringify({
        event_id: 3,
        event_type: "run_completed",
        payload_json: JSON.stringify({ status: "completed" }),
        run_id: "run-1",
        trace_id: "run-1",
    }),
});
await new Promise(resolve => setTimeout(resolve, 20));

console.log(JSON.stringify({
    fetchRecoveryCalls: globalThis.__fetchRecoveryCalls,
    eventSourceUrls: globalThis.__eventSources.map(source => source.url),
    eventSourceClosed: eventSource.closed === true,
    routeEventCalls: globalThis.__routeEventCalls,
    overlayCallCount: globalThis.__overlayCalls.length,
    hydrateCalls: globalThis.__hydrateCalls,
    scheduleSessionsRefreshCalls: globalThis.__scheduleSessionsRefreshCalls,
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

    assert payload["fetchRecoveryCalls"] == ["session-1"]
    assert payload["eventSourceUrls"] == ["/api/runs/run-1/events?after_event_id=1"]
    assert payload["eventSourceClosed"] is True
    assert payload["routeEventCalls"] == [
        {
            "evType": "todo_updated",
            "payload": {
                "run_id": "run-1",
                "session_id": "session-1",
                "items": [{"content": "Persist todo", "status": "completed"}],
                "version": 1,
            },
            "eventMeta": {
                "event_id": 2,
                "event_type": "todo_updated",
                "payload_json": '{"run_id":"run-1","session_id":"session-1","items":[{"content":"Persist todo","status":"completed"}],"version":1}',
                "run_id": "run-1",
                "trace_id": "run-1",
            },
        },
        {
            "evType": "run_completed",
            "payload": {"status": "completed"},
            "eventMeta": {
                "event_id": 3,
                "event_type": "run_completed",
                "payload_json": '{"status":"completed"}',
                "run_id": "run-1",
                "trace_id": "run-1",
            },
        },
    ]
    assert payload["overlayCallCount"] == 0
    assert payload["hydrateCalls"] == [
        {
            "sessionId": "session-1",
            "includeRounds": True,
            "roundsScrollPolicy": "completion-auto",
        }
    ]
    assert payload["scheduleSessionsRefreshCalls"] == 1


def test_pending_run_start_detaches_to_background_on_session_switch(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "stream.js"
    module_under_test_path = tmp_path / "stream.mjs"
    runner_path = tmp_path / "runner_pending_start.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./api.js", "./mockApi.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../app/prompt.js", "./mockPrompt.mjs")
        .replace("../components/subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/backendStatus.js", "./mockBackendStatus.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./eventRouter.js", "./mockEventRouter.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("./state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionRecovery() {
    return {};
}

export async function fetchSessionSubagents() {
    return [];
}

export async function fetchSessions() {
    return [];
}

export async function sendUserPrompt(sessionId, promptText) {
    globalThis.__sendUserPromptCalls.push({ sessionId, promptText });
    return new Promise(resolve => {
        globalThis.__sendUserPromptResolvers.push(resolve);
    });
}

export async function stopRun() {
    globalThis.__stopRunCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    globalThis.__contextRefreshCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPrompt.mjs").write_text(
        """
export function refreshSessionTopologyControls() {
    globalThis.__topologyRefreshCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function replaceSessionSubagents() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSidebar.mjs").write_text(
        """
export function scheduleSessionsRefresh() {
    globalThis.__scheduleSessionsRefreshCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    sendBtn: { disabled: false },
    promptInput: {
        disabled: false,
        focus() {
            globalThis.__focusCalls += 1;
        },
    },
    yoloToggle: { disabled: false },
    thinkingModeToggle: { disabled: false },
    thinkingEffortSelect: { disabled: false },
    stopBtn: { style: {}, disabled: false },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockBackendStatus.mjs").write_text(
        """
export function markBackendOnline() {
    return undefined;
}

export async function refreshBackendStatus() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { error: String(error?.message || error || ''), ...extra };
}

export function logError() {
    return undefined;
}

export function logInfo() {
    return undefined;
}

export function logWarn() {
    return undefined;
}

export function sysLog(message) {
    globalThis.__logs.push(String(message));
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockEventRouter.mjs").write_text(
        """
export function routeEvent() {
    globalThis.__routeEventCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() {
    globalThis.__clearRunStreamStateCalls += 1;
}

export function applyStreamOverlayEvent() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-a",
    currentSessionMode: "normal",
    activeSubagentSession: null,
    activeEventSource: null,
    activeRunId: null,
    isGenerating: false,
    runPrimaryRoleMap: {},
};

export function getPrimaryRoleId() {
    return "MainAgent";
}

export function getPrimaryRoleLabel() {
    return "Main Agent";
}

export function getRunPrimaryRoleId() {
    return "MainAgent";
}

export function getRunPrimaryRoleLabel() {
    return "Main Agent";
}

export function setRunPrimaryRole(runId, roleId) {
    state.runPrimaryRoleMap[runId] = roleId;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__sendUserPromptCalls = [];
globalThis.__sendUserPromptResolvers = [];
globalThis.__stopRunCalls = 0;
globalThis.__scheduleSessionsRefreshCalls = 0;
globalThis.__contextRefreshCalls = 0;
globalThis.__topologyRefreshCalls = 0;
globalThis.__focusCalls = 0;
globalThis.__logs = [];
globalThis.__eventSources = [];
globalThis.__routeEventCalls = 0;
globalThis.__clearRunStreamStateCalls = 0;

class MockEventSource {
    constructor(url) {
        this.url = url;
        this.closed = false;
        this.onmessage = null;
        this.onerror = null;
        globalThis.__eventSources.push(this);
    }

    close() {
        this.closed = true;
    }
}

globalThis.EventSource = MockEventSource;

const {
    detachActiveStreamForSessionSwitch,
    hasPendingRunCreation,
    startIntentStream,
} = await import("./stream.mjs");

const runCreated = [];
const completed = [];
const streamPromise = startIntentStream(
    "hello",
    "session-a",
    sessionId => completed.push(sessionId),
    {
        targetRoleId: "MainAgent",
        onRunCreated: run => runCreated.push(run.run_id),
    },
);
await Promise.resolve();
const pendingBeforeDetach = hasPendingRunCreation("session-a");
state.currentSessionId = "session-b";
const detached = detachActiveStreamForSessionSwitch({ focusPrompt: false });
const afterDetach = {
    isGenerating: state.isGenerating,
    sendDisabled: els.sendBtn.disabled,
    promptDisabled: els.promptInput.disabled,
    activeRunId: state.activeRunId,
    stopDisplay: els.stopBtn.style.display,
};

globalThis.__sendUserPromptResolvers[0]({
    run_id: "run-a",
    target_role_id: "MainAgent",
});
await streamPromise;

console.log(JSON.stringify({
    pendingBeforeDetach,
    detached,
    afterDetach,
    currentSessionId: state.currentSessionId,
    activeRunId: state.activeRunId,
    activeEventSourceUrl: state.activeEventSource?.url || null,
    eventSourceUrls: globalThis.__eventSources.map(source => source.url),
    runCreated,
    completed,
    scheduleSessionsRefreshCalls: globalThis.__scheduleSessionsRefreshCalls,
    sendUserPromptCalls: globalThis.__sendUserPromptCalls,
    stopRunCalls: globalThis.__stopRunCalls,
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

    assert payload["pendingBeforeDetach"] is True
    assert payload["detached"] is True
    assert payload["afterDetach"] == {
        "isGenerating": False,
        "sendDisabled": False,
        "promptDisabled": False,
        "activeRunId": None,
        "stopDisplay": "none",
    }
    assert payload["currentSessionId"] == "session-b"
    assert payload["activeRunId"] is None
    assert payload["activeEventSourceUrl"] is None
    assert payload["eventSourceUrls"] == ["/api/runs/run-a/events"]
    assert payload["runCreated"] == []
    assert payload["completed"] == []
    assert payload["scheduleSessionsRefreshCalls"] == 1
    assert payload["sendUserPromptCalls"] == [
        {"sessionId": "session-a", "promptText": "hello"},
    ]
    assert payload["stopRunCalls"] == 0


def test_normal_mode_subagent_discovery_reconciles_sidebar_cache(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "stream.js"
    module_under_test_path = tmp_path / "stream.mjs"
    runner_path = tmp_path / "runner_discovery.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./api.js", "./mockApi.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../app/prompt.js", "./mockPrompt.mjs")
        .replace("../components/subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/backendStatus.js", "./mockBackendStatus.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./eventRouter.js", "./mockEventRouter.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("./state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionRecovery() {
    return {};
}

export async function fetchSessionSubagents() {
    return [
        {
            instance_id: "inst-sub-1",
            role_id: "Explorer",
            run_id: "subagent_run_1",
            status: "running",
            run_status: "running",
            checkpoint_event_id: 3,
        },
    ];
}

export async function fetchSessions() {
    return [];
}

export async function sendUserPrompt() {
    throw new Error("not used");
}

export async function stopRun() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPrompt.mjs").write_text(
        """
export function refreshSessionTopologyControls() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function replaceSessionSubagents(sessionId, payload, options = {}) {
    globalThis.__replaceCalls.push({
        sessionId,
        rowCount: Array.isArray(payload) ? payload.length : 0,
        runId: Array.isArray(payload) ? payload[0]?.run_id || null : null,
        emitChange: options.emitChange === true,
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSidebar.mjs").write_text(
        """
export function scheduleSessionsRefresh() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    sendBtn: { disabled: false },
    promptInput: { disabled: false, focus() {} },
    yoloToggle: { disabled: false },
    thinkingModeToggle: { disabled: false },
    thinkingEffortSelect: { disabled: false },
    stopBtn: { style: {}, disabled: false },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockBackendStatus.mjs").write_text(
        """
export function markBackendOnline() {
    return undefined;
}

export async function refreshBackendStatus() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { error: String(error?.message || error || ''), ...extra };
}

export function logError() {
    return undefined;
}

export function logInfo() {
    return undefined;
}

export function logWarn() {
    return undefined;
}

export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockEventRouter.mjs").write_text(
        """
export function routeEvent() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    activeSubagentSession: null,
    activeEventSource: null,
    activeRunId: "run-main",
    isGenerating: true,
};

export function getPrimaryRoleId() {
    return "MainAgent";
}

export function getPrimaryRoleLabel() {
    return "Main Agent";
}

export function getRunPrimaryRoleId() {
    return "MainAgent";
}

export function getRunPrimaryRoleLabel() {
    return "Main Agent";
}

export function setRunPrimaryRole() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
globalThis.__replaceCalls = [];

const { scheduleCurrentSessionSubagentDiscovery } = await import("./stream.mjs");

scheduleCurrentSessionSubagentDiscovery({ delayMs: 0 });
await new Promise(resolve => setTimeout(resolve, 20));

console.log(JSON.stringify({
    replaceCalls: globalThis.__replaceCalls,
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

    assert payload["replaceCalls"] == [
        {
            "sessionId": "session-1",
            "rowCount": 1,
            "runId": "subagent_run_1",
            "emitChange": True,
        }
    ]
