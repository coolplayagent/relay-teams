# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_retry_timeline_escapes_fallback_target_markup(tmp_path: Path) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
const { renderRetryEventMarkup } = await import('./timeline.mjs');

const html = renderRetryEventMarkup(
    {
        kind: 'fallback',
        phase: 'scheduled',
        to_profile_id: '<img src=x onerror=alert(1)>',
    },
    Date.now(),
);

console.log(JSON.stringify({ html }));
""".strip(),
    )

    html = str(payload["html"])
    assert "<img src=x onerror=alert(1)>" not in html
    assert (
        '<span class="round-retry-copy">Switched to &lt;img src=x onerror=alert(1)&gt;</span>'
        in html
    )


def test_retry_timeline_renders_stable_retry_item_with_spinner(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
const { renderRetryEventMarkup } = await import('./timeline.mjs');

const html = renderRetryEventMarkup(
    {
        event_id: 'retry-run-1-2',
        kind: 'retry',
        phase: 'retrying',
        is_active: true,
        attempt_number: 2,
        total_attempts: 6,
        retry_in_ms: 1000,
        error_code: 'rate_limit',
    },
    Date.now(),
);

console.log(JSON.stringify({ html }));
""".strip(),
    )

    html = str(payload["html"])
    assert 'data-retry-event-id="retry-run-1-2"' in html
    assert "round-retry-item-active" in html
    assert "round-retry-item-retrying" in html
    assert "round-retry-spinner" in html
    assert "Attempt 2/6 in progress" in html


def test_load_session_rounds_uses_full_timeline_page_for_navigator(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.__initialRoundsPage = {
    items: [
        { run_id: 'run-5', created_at: '2026-04-25T11:05:00', intent: 'Latest' },
        { run_id: 'run-4', created_at: '2026-04-25T11:04:00', intent: 'Previous' },
    ],
    has_more: true,
    next_cursor: 'run-4',
};
globalThis.__timelineRoundsPage = {
    items: [
        { run_id: 'run-5', created_at: '2026-04-25T11:05:00', intent: 'Latest' },
        { run_id: 'run-4', created_at: '2026-04-25T11:04:00', intent: 'Previous' },
        { run_id: 'run-3', created_at: '2026-04-25T11:03:00', intent: 'Older' },
        { run_id: 'run-2', created_at: '2026-04-25T11:02:00', intent: 'Older still' },
        { run_id: 'run-1', created_at: '2026-04-25T11:01:00', intent: 'Oldest' },
    ],
    has_more: false,
    next_cursor: null,
};

const { loadSessionRounds } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

await loadSessionRounds('session-1', { render: false });

console.log(JSON.stringify({
    currentRunIds: roundsState.currentRounds.map(round => round.run_id),
    timelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
    navigatorRunIds: globalThis.__navigatorRounds.map(round => round.run_id),
    pagingHasMore: roundsState.paging.hasMore,
}));
""".strip(),
    )

    assert payload == {
        "currentRunIds": ["run-4", "run-5"],
        "timelineRunIds": ["run-1", "run-2", "run-3", "run-4", "run-5"],
        "navigatorRunIds": ["run-1", "run-2", "run-3", "run-4", "run-5"],
        "pagingHasMore": True,
    }


def test_load_session_rounds_falls_back_when_timeline_page_fails(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.__initialRoundsPage = {
    items: [
        { run_id: 'run-2', created_at: '2026-04-25T11:02:00', intent: 'Latest' },
        { run_id: 'run-1', created_at: '2026-04-25T11:01:00', intent: 'Older' },
    ],
    has_more: true,
    next_cursor: 'run-1',
};
globalThis.__timelineRoundsPageError = new Error('timeline unavailable');

const { loadSessionRounds } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

await loadSessionRounds('session-1', { render: false });

console.log(JSON.stringify({
    currentRunIds: roundsState.currentRounds.map(round => round.run_id),
    timelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
    navigatorRunIds: globalThis.__navigatorRounds.map(round => round.run_id),
    loggedCodes: globalThis.__loggedErrors.map(entry => entry.code),
}));
""".strip(),
    )

    assert payload == {
        "currentRunIds": ["run-1", "run-2"],
        "timelineRunIds": ["run-1", "run-2"],
        "navigatorRunIds": ["run-1", "run-2"],
        "loggedCodes": ["frontend.rounds.timeline_load_failed"],
    }


def test_load_session_rounds_renders_page_before_slow_timeline_payload(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.__initialRoundsPage = {
    items: [
        { run_id: 'run-2', created_at: '2026-04-25T11:02:00', intent: 'Latest' },
        { run_id: 'run-1', created_at: '2026-04-25T11:01:00', intent: 'Older' },
    ],
    has_more: true,
    next_cursor: 'run-1',
};
globalThis.__timelineRoundsPagePromise = new Promise(resolve => {
    globalThis.__resolveTimelineRoundsPage = resolve;
});

const { loadSessionRounds } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

const loadPromise = loadSessionRounds('session-1', { render: false });
await Promise.resolve();
await Promise.resolve();

const beforeTimeline = {
    currentRunIds: roundsState.currentRounds.map(round => round.run_id),
    timelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
    navigatorSnapshots: globalThis.__navigatorRoundSnapshots.map(snapshot =>
        snapshot.map(round => round.run_id)
    ),
};

globalThis.__resolveTimelineRoundsPage({
    items: [
        { run_id: 'run-3', created_at: '2026-04-25T11:03:00', intent: 'Newest' },
        { run_id: 'run-2', created_at: '2026-04-25T11:02:00', intent: 'Latest' },
        { run_id: 'run-1', created_at: '2026-04-25T11:01:00', intent: 'Older' },
    ],
    has_more: false,
    next_cursor: null,
});
await loadPromise;

console.log(JSON.stringify({
    beforeTimeline,
    afterTimelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
    navigatorSnapshots: globalThis.__navigatorRoundSnapshots.map(snapshot =>
        snapshot.map(round => round.run_id)
    ),
}));
""".strip(),
    )

    assert payload == {
        "beforeTimeline": {
            "currentRunIds": ["run-1", "run-2"],
            "timelineRunIds": ["run-1", "run-2"],
            "navigatorSnapshots": [["run-1", "run-2"]],
        },
        "afterTimelineRunIds": ["run-1", "run-2", "run-3"],
        "navigatorSnapshots": [["run-1", "run-2"], ["run-1", "run-2", "run-3"]],
    }


def test_load_session_rounds_ignores_stale_timeline_after_session_switch(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.__initialRoundsPage = {
    items: [
        { run_id: 'run-2', created_at: '2026-04-25T11:02:00', intent: 'Latest' },
        { run_id: 'run-1', created_at: '2026-04-25T11:01:00', intent: 'Older' },
    ],
    has_more: true,
    next_cursor: 'run-1',
};
globalThis.__timelineRoundsPagePromise = new Promise(resolve => {
    globalThis.__resolveTimelineRoundsPage = resolve;
});

const { state } = await import('./mockState.mjs');
const { loadSessionRounds } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

const loadPromise = loadSessionRounds('session-1', { render: false });
await Promise.resolve();
await Promise.resolve();
state.currentSessionId = 'session-2';

globalThis.__resolveTimelineRoundsPage({
    items: [
        { run_id: 'foreign-run', created_at: '2026-04-25T12:00:00', intent: 'Foreign' },
    ],
    has_more: false,
    next_cursor: null,
});
await loadPromise;

console.log(JSON.stringify({
    currentRunIds: roundsState.currentRounds.map(round => round.run_id),
    timelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
    navigatorSnapshots: globalThis.__navigatorRoundSnapshots.map(snapshot =>
        snapshot.map(round => round.run_id)
    ),
}));
""".strip(),
    )

    assert payload == {
        "currentRunIds": ["run-1", "run-2"],
        "timelineRunIds": ["run-1", "run-2"],
        "navigatorSnapshots": [["run-1", "run-2"]],
    }


def test_load_session_rounds_evicts_live_round_when_persisted_without_messages(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.document = { getElementById: () => null };
const { state } = await import('./mockState.mjs');
const { createLiveRound, loadSessionRounds } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

state.activeSubagentSession = { sessionId: 'session-1' };
createLiveRound('run-1', 'approval-only run');

globalThis.__initialRoundsPage = {
    items: [
        {
            run_id: 'run-1',
            created_at: '2026-04-25T11:01:00',
            intent: 'approval-only run',
            run_status: 'completed',
            run_phase: 'completed',
            coordinator_messages: [],
            has_user_messages: true,
        },
    ],
    has_more: false,
    next_cursor: null,
};
globalThis.__timelineRoundsPage = globalThis.__initialRoundsPage;

await loadSessionRounds('session-1', { render: false });
const afterPersist = roundsState.currentRounds.map(round => ({
    run_id: round.run_id,
    status: round.run_status,
    liveOnly: round.__liveOnly === true,
}));

globalThis.__initialRoundsPage = {
    items: [
        { run_id: 'run-2', created_at: '2026-04-25T11:02:00', intent: 'newer' },
    ],
    has_more: false,
    next_cursor: null,
};
globalThis.__timelineRoundsPage = globalThis.__initialRoundsPage;

await loadSessionRounds('session-1', { render: false });

console.log(JSON.stringify({
    afterPersist,
    currentRunIds: roundsState.currentRounds.map(round => round.run_id),
    timelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
}));
""".strip(),
    )

    assert payload == {
        "afterPersist": [
            {"run_id": "run-1", "status": "completed", "liveOnly": False},
        ],
        "currentRunIds": ["run-2"],
        "timelineRunIds": ["run-2"],
    }


def test_terminal_round_refresh_waits_for_expected_tool_calls_from_history(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.document = { getElementById: () => null };
globalThis.__sessionRoundResponses = [
    {
        run_id: 'run-1',
        created_at: '2026-04-25T11:01:00',
        run_status: 'completed',
        coordinator_messages: [
            {
                message: {
                    parts: [
                        { part_kind: 'tool-call', tool_call_id: 'call-1', tool_name: 'spawn_subagent' },
                    ],
                },
            },
        ],
    },
    {
        run_id: 'run-1',
        created_at: '2026-04-25T11:01:00',
        run_status: 'completed',
        coordinator_messages: [
            {
                message: {
                    parts: [
                        { part_kind: 'tool-call', tool_call_id: 'call-1', tool_name: 'spawn_subagent' },
                        { part_kind: 'tool-call', tool_call_id: 'call-2', tool_name: 'spawn_subagent' },
                        { part_kind: 'tool-call', tool_call_id: 'call-3', tool_name: 'spawn_subagent' },
                    ],
                },
            },
        ],
    },
];

const { state } = await import('./mockState.mjs');
const { createLiveRound, refreshTerminalRoundFromHistory } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

state.currentSessionId = 'session-1';
state.activeSubagentSession = { sessionId: 'session-1' };
createLiveRound('run-1', 'live run');

await refreshTerminalRoundFromHistory('session-1', 'run-1', {
    expectedToolCallIds: ['call-1', 'call-2', 'call-3'],
    render: false,
});

const persistedRound = roundsState.currentRounds.find(round => round.run_id === 'run-1');
const toolCallIds = persistedRound.coordinator_messages.flatMap(message =>
    message.message.parts.filter(part => part.part_kind === 'tool-call').map(part => part.tool_call_id)
);

console.log(JSON.stringify({
    fetchAttempts: globalThis.__sessionRoundFetches,
    clearedRuns: globalThis.__clearedRuns,
    liveOnly: persistedRound.__liveOnly === true,
    runStatus: persistedRound.run_status,
    toolCallIds,
}));
""".strip(),
    )

    assert payload == {
        "fetchAttempts": 2,
        "clearedRuns": ["run-1"],
        "liveOnly": False,
        "runStatus": "completed",
        "toolCallIds": ["call-1", "call-2", "call-3"],
    }


def test_terminal_round_refresh_retries_transient_fetch_errors(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.document = { getElementById: () => null };
globalThis.__sessionRoundResponses = [
    { errorStatus: 404 },
    { errorStatus: 503 },
    {
        run_id: 'run-1',
        created_at: '2026-04-25T11:01:00',
        run_status: 'completed',
        coordinator_messages: [
            {
                message: {
                    parts: [
                        { part_kind: 'tool-call', tool_call_id: 'call-1', tool_name: 'spawn_subagent' },
                        { part_kind: 'tool-call', tool_call_id: 'call-2', tool_name: 'spawn_subagent' },
                    ],
                },
            },
        ],
    },
];

const { state } = await import('./mockState.mjs');
const { refreshTerminalRoundFromHistory } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

state.currentSessionId = 'session-1';
await refreshTerminalRoundFromHistory('session-1', 'run-1', {
    expectedToolCallIds: ['call-1', 'call-2'],
    render: false,
});

const persistedRound = roundsState.currentRounds.find(round => round.run_id === 'run-1');
const toolCallIds = persistedRound.coordinator_messages.flatMap(message =>
    message.message.parts.filter(part => part.part_kind === 'tool-call').map(part => part.tool_call_id)
);

console.log(JSON.stringify({
    fetchAttempts: globalThis.__sessionRoundFetches,
    clearedRuns: globalThis.__clearedRuns,
    runStatus: persistedRound.run_status,
    toolCallIds,
}));
""".strip(),
    )

    assert payload == {
        "fetchAttempts": 3,
        "clearedRuns": ["run-1"],
        "runStatus": "completed",
        "toolCallIds": ["call-1", "call-2"],
    }


def test_terminal_round_refresh_does_not_merge_after_session_switch(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.document = { getElementById: () => null };
let resolveRound;
globalThis.__sessionRoundResponses = [
    new Promise(resolve => {
        resolveRound = resolve;
    }),
];

const { state } = await import('./mockState.mjs');
const { refreshTerminalRoundFromHistory } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

state.currentSessionId = 'session-1';
const refreshPromise = refreshTerminalRoundFromHistory('session-1', 'run-1', {
    expectedToolCallIds: ['call-1', 'call-2'],
});
await Promise.resolve();

state.currentSessionId = 'session-2';
roundsState.currentRounds = [
    { run_id: 'run-2', created_at: '2026-04-25T11:02:00', coordinator_messages: [] },
];
roundsState.timelineRounds = roundsState.currentRounds;

resolveRound({
    run_id: 'run-1',
    created_at: '2026-04-25T11:01:00',
    run_status: 'completed',
    coordinator_messages: [
        {
            message: {
                parts: [
                    { part_kind: 'tool-call', tool_call_id: 'call-1', tool_name: 'spawn_subagent' },
                    { part_kind: 'tool-call', tool_call_id: 'call-2', tool_name: 'spawn_subagent' },
                ],
            },
        },
    ],
});
await refreshPromise;

console.log(JSON.stringify({
    fetchAttempts: globalThis.__sessionRoundFetches,
    clearedRuns: globalThis.__clearedRuns || [],
    currentRunIds: roundsState.currentRounds.map(round => round.run_id),
    timelineRunIds: roundsState.timelineRounds.map(round => round.run_id),
}));
""".strip(),
    )

    assert payload == {
        "fetchAttempts": 1,
        "clearedRuns": [],
        "currentRunIds": ["run-2"],
        "timelineRunIds": ["run-2"],
    }


def test_terminal_round_refresh_caps_incomplete_history_followups(
    tmp_path: Path,
) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
globalThis.document = { getElementById: () => null };
globalThis.__followupTimers = 0;
globalThis.__followupCallbacks = [];
globalThis.setTimeout = (callback, delayMs) => {
    if (delayMs === 900) {
        globalThis.__followupTimers += 1;
        globalThis.__followupCallbacks.push(callback);
        return globalThis.__followupTimers;
    }
    Promise.resolve().then(callback);
    return globalThis.__followupTimers;
};
globalThis.__sessionRoundResponses = Array.from({ length: 40 }, () => ({
    run_id: 'run-1',
    created_at: '2026-04-25T11:01:00',
    run_status: 'completed',
    coordinator_messages: [
        {
            message: {
                parts: [
                    { part_kind: 'tool-call', tool_call_id: 'call-1', tool_name: 'spawn_subagent' },
                ],
            },
        },
    ],
}));

const { refreshTerminalRoundFromHistory } = await import('./timeline.mjs');
const { roundsState } = await import('./mockRoundsState.mjs');

await refreshTerminalRoundFromHistory('session-1', 'run-1', {
    expectedToolCallIds: ['call-1', 'call-2'],
});
while (globalThis.__followupCallbacks.length > 0) {
    const callback = globalThis.__followupCallbacks.shift();
    callback();
    for (let index = 0; index < 20; index += 1) {
        await Promise.resolve();
    }
}

console.log(JSON.stringify({
    fetchAttempts: globalThis.__sessionRoundFetches,
    followupTimers: globalThis.__followupTimers,
    currentRounds: roundsState.currentRounds.length,
}));
""".strip(),
    )

    assert payload == {
        "fetchAttempts": 24,
        "followupTimers": 3,
        "currentRounds": 0,
    }


def _run_round_timeline_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    )

    module_under_test_path = tmp_path / "timeline.mjs"
    runner_path = tmp_path / "runner-timeline.mjs"

    replacements = {
        "../../utils/dom.js": "./mockDom.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../core/api.js": "./mockApi.mjs",
        "../agentPanel.js": "./mockAgentPanel.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "../messageRenderer/helpers/prompt.js": "./mockPromptHelpers.mjs",
        "./navigator.js": "./mockNavigator.mjs",
        "./paging.js": "./mockPaging.mjs",
        "./scrollController.js": "./mockScrollController.mjs",
        "./state.js": "./mockRoundsState.mjs",
        "./todo.js": "./mockTodo.mjs",
        "./utils.js": "./mockRoundUtils.mjs",
        "../../utils/promptTokens.js": "./mockPromptTokens.mjs",
        "../../utils/logger.js": "./mockLogger.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    source_text = source_text.replace(
        "function renderRetryEventMarkup(event, nowMs) {",
        "export function renderRetryEventMarkup(event, nowMs) {",
        1,
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    chatMessages: {
        scrollHeight: 0,
        scrollTop: 0,
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    activeSubagentSession: null,
    currentSessionId: 'session-1',
};

export function getRunPrimaryRoleId() {
    return null;
}

export function getRunPrimaryRoleLabel() {
    return 'Main Agent';
}

export function isRunPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchRunTokenUsage() {
    return {};
}

export async function fetchSessionRound() {
    globalThis.__sessionRoundFetches = (globalThis.__sessionRoundFetches || 0) + 1;
    if (Array.isArray(globalThis.__sessionRoundResponses)) {
        const response = globalThis.__sessionRoundResponses.shift() || null;
        if (response?.errorStatus) {
            const error = new Error('transient fetch error');
            error.status = response.errorStatus;
            throw error;
        }
        return response;
    }
    return globalThis.__sessionRound || null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function setRoundPendingApprovals() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearAllStreamState() {
    return undefined;
}

export function clearRunStreamState() {
    if (!Array.isArray(globalThis.__clearedRuns)) {
        globalThis.__clearedRuns = [];
    }
    globalThis.__clearedRuns.push(arguments[0]);
    return undefined;
}

export function getCoordinatorStreamOverlay() {
    return null;
}

export function bindCopyButton() {
    return null;
}

export function renderHistoricalMessageList() {
    return undefined;
}

export function syncLastAnswerCopyButton() {
    return undefined;
}

export function reconcileTerminalRunStreamState(runId) {
    globalThis.__reconciledTerminalRuns.push(runId);
}

export function getOrCreateStreamBlock() {
    return undefined;
}

export function appendStreamChunk() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPromptHelpers.mjs").write_text(
        """
export function normalizePromptContentParts(parts) {
    return Array.isArray(parts) ? parts : [];
}

export function renderPromptContentParts() {
    return undefined;
}

export function summarizePromptContentParts() {
    return '';
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function renderRoundNavigator(rounds) {
    globalThis.__navigatorRounds = Array.isArray(rounds) ? rounds : [];
    if (!Array.isArray(globalThis.__navigatorRoundSnapshots)) {
        globalThis.__navigatorRoundSnapshots = [];
    }
    globalThis.__navigatorRoundSnapshots.push(globalThis.__navigatorRounds);
    return undefined;
}

export function clearRoundNavigator() {
    return undefined;
}

export function setActiveRoundNav() {
    return undefined;
}

export function patchRoundNavigatorTodo() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPaging.mjs").write_text(
        """
export function applyRoundPage(page, { prepend } = { prepend: false }) {
    const rawItems = Array.isArray(page?.items) ? page.items : [];
    const sortedItems = sortRoundsAscending(rawItems);
    if (!prepend) {
        globalThis.__roundsState.currentRounds = sortedItems;
    } else {
        const existing = new Set(globalThis.__roundsState.currentRounds.map(round => round.run_id));
        globalThis.__roundsState.currentRounds = [
            ...sortedItems.filter(round => !existing.has(round.run_id)),
            ...globalThis.__roundsState.currentRounds,
        ];
    }
    globalThis.__roundsState.paging = {
        hasMore: Boolean(page?.has_more),
        nextCursor: page?.next_cursor || null,
        loading: false,
    };
}

export function applyTimelineRoundPage(page) {
    globalThis.__roundsState.timelineRounds = sortRoundsAscending(
        Array.isArray(page?.items) ? page.items : [],
    );
}

export function sortRoundsAscending(rounds) {
    return (Array.isArray(rounds) ? rounds : []).slice().sort((a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
}

export async function fetchInitialRoundsPage() {
    return globalThis.__initialRoundsPage || { items: [] };
}

export async function fetchTimelineRoundsPage() {
    if (globalThis.__timelineRoundsPagePromise) {
        return await globalThis.__timelineRoundsPagePromise;
    }
    if (globalThis.__timelineRoundsPageError) {
        throw globalThis.__timelineRoundsPageError;
    }
    return globalThis.__timelineRoundsPage || { items: [] };
}

export async function fetchOlderRoundsPage() {
    return globalThis.__olderRoundsPage || { items: [] };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockScrollController.mjs").write_text(
        """
export function captureChatScrollAnchor() {
    return null;
}

export function restoreChatScrollAnchor() {
    return false;
}

export function shouldFollowLatestRoundAfterCompletion() {
    return true;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRoundsState.mjs").write_text(
        """
export const roundsState = globalThis.__roundsState = {
    currentRound: null,
    currentRounds: [],
    timelineRounds: [],
    activeRunId: null,
    activeVisibility: 0,
    activeLockUntil: 0,
    pendingScrollTargetRunId: null,
    pendingScrollUnlockAt: 0,
    paging: {
        hasMore: false,
        nextCursor: null,
        loading: false,
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockTodo.mjs").write_text(
        """
export function normalizeRoundTodoSnapshot() {
    return null;
}

export function areRoundTodoSnapshotsEqual(left, right) {
    return left === right;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRoundUtils.mjs").write_text(
        """
export function roundSectionId(runId) {
    return String(runId || '');
}

export function esc(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

export function roundStateLabel() {
    return '';
}

export function roundStateTone() {
    return 'idle';
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload() {
    return {};
}

globalThis.__loggedErrors = [];
globalThis.__reconciledTerminalRuns = [];

export function logError(code, message, payload) {
    globalThis.__loggedErrors.push({ code, message, payload });
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
const messages = {
    'rounds.retry.scheduled_label': 'Retry scheduled',
    'rounds.retry.retrying_label': 'Retrying',
    'rounds.retry.failed_label': 'Retry failed',
    'rounds.retry.succeeded_label': 'Retry resumed',
    'rounds.retry.scheduled_copy': 'Attempt {attempt}/{total} in {seconds}',
    'rounds.retry.retrying_copy': 'Attempt {attempt}/{total} in progress',
    'rounds.retry.failed_copy': 'Attempt {attempt}/{total} failed',
    'rounds.retry.succeeded_copy': 'Attempt {attempt}/{total} resumed',
    'rounds.retry.fallback_label': 'Fallback',
    'rounds.retry.fallback_failed_label': 'Fallback failed',
    'rounds.retry.fallback_activated_copy': 'Fallback activated',
    'rounds.retry.fallback_failed_copy': 'No fallback candidate succeeded',
    'rounds.retry.fallback_switched_copy': 'Switched to {target}',
};

export function formatMessage(message, values = {}) {
    return Object.entries(values).reduce(
        (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
        t(message),
    );
}

export function t(key) {
    return messages[key] || String(key || '');
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPromptTokens.mjs").write_text(
        """
export function renderPromptTokenizedText(targetEl, source) {
    if (targetEl) {
        targetEl.textContent = String(source || '');
    }
    return targetEl;
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
        timeout=3,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
