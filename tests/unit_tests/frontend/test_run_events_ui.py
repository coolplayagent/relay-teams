# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


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


def test_run_started_marks_sidebar_session_running_immediately(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunStarted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';

handleRunStarted({ run_id: 'run-1', session_id: 'session-1' });

await Promise.resolve();

console.log(JSON.stringify({
    activeCalls: globalThis.__markSidebarSessionRunActiveCalls,
    dispatchedEvents: globalThis.__documentDispatches,
}));
""".strip(),
    )

    assert payload["activeCalls"] == [
        {
            "sessionId": "session-1",
            "detail": {"runId": "run-1", "status": "running"},
        }
    ]
    assert payload["dispatchedEvents"] == [
        {
            "type": "agent-teams-session-run-active",
            "detail": {
                "sessionId": "session-1",
                "runId": "run-1",
                "status": "running",
            },
        }
    ]


def test_terminal_run_event_marks_current_main_session_viewed(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunCompleted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';

handleRunCompleted({ run_id: 'run-1' });

await Promise.resolve();

console.log(JSON.stringify({
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
    terminalCalls: globalThis.__markSidebarSessionRunTerminalCalls,
}));
""".strip(),
    )

    assert payload["viewedCalls"] == ["session-1"]
    assert payload["terminalCalls"] == [
        {
            "sessionId": "session-1",
            "detail": {
                "runId": "run-1",
                "status": "completed",
                "viewed": True,
            },
        }
    ]


def test_run_stopped_keeps_terminal_run_unviewed_for_resume_context(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunStopped } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';

handleRunStopped({ run_id: 'run-1', session_id: 'session-1' }, {});

await Promise.resolve();

console.log(JSON.stringify({
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
    stoppedCalls: globalThis.__markNormalModeSubagentSessionsStoppedForParentCalls,
}));
""".strip(),
    )

    assert payload["viewedCalls"] == []
    assert payload["stoppedCalls"] == ["session-1"]


def test_run_started_does_not_reactivate_stopped_subagent_sessions(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunStarted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';

handleRunStarted({ run_id: 'run-1', session_id: 'session-1' });

await Promise.resolve();

console.log(JSON.stringify({
    clearedCalls: globalThis.__clearNormalModeSubagentParentStopStateCalls,
    runningCalls: globalThis.__markNormalModeSubagentSessionsRunningForParentCalls,
}));
""".strip(),
    )

    assert payload["clearedCalls"] == ["session-1"]
    assert payload["runningCalls"] == []


def test_run_resumed_reactivates_parent_stopped_subagent_sessions(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';

routeEvent('run_resumed', {}, { run_id: 'run-1', session_id: 'session-1' });

await Promise.resolve();

console.log(JSON.stringify({
    calls: globalThis.__runEventCalls.filter(call => call.name === 'handleRunStarted'),
}));
""".strip(),
    )

    assert payload["calls"] == [
        {
            "name": "handleRunStarted",
            "args": [
                {"run_id": "run-1", "session_id": "session-1"},
                {"resumeSubagents": True},
            ],
        }
    ]


def test_subagent_session_status_event_routes_to_subagent_session_cache(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

routeEvent('subagent_session_status_changed', {
    parent_session_id: 'session-1',
    parent_run_id: 'run-1',
    subagent_run_id: 'subagent_run_1',
    subagent_instance_id: 'inst-sub-1',
    subagent_role_id: 'Explorer',
    status: 'stopped',
}, {
    run_id: 'run-1',
    session_id: 'session-1',
    event_id: 'evt-1',
});

console.log(JSON.stringify({
    statusEvents: globalThis.__subagentSessionStatusEvents,
    backgroundEvents: globalThis.__applyBackgroundTaskEventCalls,
}));
""".strip(),
    )

    assert payload["statusEvents"] == [
        {
            "payload": {
                "parent_session_id": "session-1",
                "parent_run_id": "run-1",
                "subagent_run_id": "subagent_run_1",
                "subagent_instance_id": "inst-sub-1",
                "subagent_role_id": "Explorer",
                "status": "stopped",
            },
            "eventMeta": {
                "run_id": "run-1",
                "session_id": "session-1",
                "event_id": "evt-1",
            },
        }
    ]
    assert payload["backgroundEvents"] == []


def test_terminal_run_event_marks_parent_session_when_subagent_view_is_open(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunCompleted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeSubagentSession = { sessionId: 'session-1', instanceId: 'agent-1' };
state.activeRunId = 'run-1';

handleRunCompleted({ run_id: 'run-1', session_id: 'session-1' });

await Promise.resolve();

console.log(JSON.stringify({
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
}));
""".strip(),
    )

    assert payload["viewedCalls"] == ["session-1"]


def test_terminal_run_event_does_not_mark_current_session_for_other_session_event(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunCompleted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-2';

handleRunCompleted({ run_id: 'run-2', session_id: 'session-2' });

await Promise.resolve();

console.log(JSON.stringify({
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
    terminalCalls: globalThis.__markSidebarSessionRunTerminalCalls,
}));
""".strip(),
    )

    assert payload["viewedCalls"] == []
    assert payload["terminalCalls"] == [
        {
            "sessionId": "session-2",
            "detail": {
                "runId": "run-2",
                "status": "completed",
                "viewed": False,
            },
        }
    ]


def test_terminal_run_event_does_not_mark_background_session_without_current_session(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunCompleted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = null;
state.activeRunId = null;

handleRunCompleted({ run_id: 'run-2', session_id: 'session-2' });

await Promise.resolve();

console.log(JSON.stringify({
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
}));
""".strip(),
    )

    assert payload["viewedCalls"] == []


def test_terminal_run_event_retries_deferred_view_mark(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunCompleted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';
globalThis.__markSessionTerminalRunViewedResponses = [
    { status: 'deferred' },
    { status: 'ok' },
];

handleRunCompleted({ run_id: 'run-1' });

await new Promise(resolve => setTimeout(resolve, 300));

console.log(JSON.stringify({
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
}));
""".strip(),
    )

    assert payload["viewedCalls"] == ["session-1", "session-1"]


def test_terminal_run_event_retries_overloaded_view_mark(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleRunCompleted } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.activeRunId = 'run-1';
globalThis.__markSessionTerminalRunViewedResponses = [
    { errorStatus: 503 },
    { status: 'ok' },
];

handleRunCompleted({ run_id: 'run-1' });

await new Promise(resolve => setTimeout(resolve, 300));

console.log(JSON.stringify({
    logs: globalThis.__sysLogCalls,
    viewedCalls: globalThis.__markSessionTerminalRunViewedCalls,
}));
""".strip(),
    )

    logs = payload["logs"]
    assert isinstance(logs, list)
    assert not any("Failed to mark session run viewed" in str(log) for log in logs)
    assert payload["viewedCalls"] == ["session-1", "session-1"]


def test_route_event_preserves_stream_block_order_without_missing_events(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

const events = [
    ['thinking_started', { text: '' }, 1],
    ['thinking_delta', { text: 'plan' }, 2],
    ['thinking_finished', {}, 3],
    ['tool_call', { tool_name: 'read', tool_call_id: 'call-1' }, 4],
    ['tool_result', { tool_name: 'read', tool_call_id: 'call-1', result: 'ok' }, 5],
    ['text_delta', { text: 'done' }, 6],
];
for (const [eventType, eventPayload, eventId] of events) {
    routeEvent(eventType, eventPayload, {
        event_id: eventId,
        run_id: 'run-1',
        trace_id: 'trace-1',
        instance_id: 'instance-main',
        role_id: 'MainAgent',
    });
}

console.log(JSON.stringify({
    runEventCalls: globalThis.__runEventCalls.map(call => call.name),
    toolEventCalls: globalThis.__toolEventCalls.map(call => call.name),
    combinedCalls: globalThis.__combinedEventCalls.map(call => call.name),
}));
""".strip(),
    )

    assert payload["runEventCalls"] == [
        "handleThinkingStarted",
        "handleThinkingDelta",
        "handleThinkingFinished",
        "handleTextDelta",
    ]
    assert payload["toolEventCalls"] == ["handleToolCall", "handleToolResult"]
    assert payload["combinedCalls"] == [
        "handleThinkingStarted",
        "handleThinkingDelta",
        "handleThinkingFinished",
        "handleToolCall",
        "handleToolResult",
        "handleTextDelta",
    ]


def test_route_event_deduplicates_replayed_stream_event_ids(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

const eventMeta = {
    event_id: 7,
    run_id: 'run-1',
    trace_id: 'trace-1',
    instance_id: 'instance-main',
    role_id: 'MainAgent',
};
routeEvent('text_delta', { text: 'once' }, eventMeta);
routeEvent('text_delta', { text: 'once again' }, eventMeta);
routeEvent('tool_result', { tool_name: 'read', tool_call_id: 'call-1' }, eventMeta);

console.log(JSON.stringify({
    runEventCalls: globalThis.__runEventCalls.map(call => call.name),
    toolEventCalls: globalThis.__toolEventCalls.map(call => call.name),
    combinedCalls: globalThis.__combinedEventCalls.map(call => call.name),
}));
""".strip(),
    )

    assert payload["runEventCalls"] == ["handleTextDelta"]
    assert payload["toolEventCalls"] == []
    assert payload["combinedCalls"] == ["handleTextDelta"]


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
routeEvent('text_delta', { text: 'non-prefix' }, {
    run_id: 'delegated-run-deadbeef',
    trace_id: 'delegated-run-deadbeef',
    normal_mode_subagent_event: true,
});

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
    assert payload["tokenUsageCalls"] == [{"immediate": False}]
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
        },
        {
            "name": "handleTextDelta",
            "args": [
                {"text": "non-prefix"},
                {
                    "run_id": "delegated-run-deadbeef",
                    "trace_id": "delegated-run-deadbeef",
                    "normal_mode_subagent_event": True,
                },
                None,
                None,
            ],
        },
    ]


def test_route_event_does_not_refresh_recovery_for_high_frequency_deltas(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

routeEvent('output_delta', {}, { run_id: 'run-1', trace_id: 'run-1' });
routeEvent('generation_progress', {}, { run_id: 'run-1', trace_id: 'run-1' });

await Promise.resolve();

console.log(JSON.stringify({
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    runEventCalls: globalThis.__runEventCalls,
}));
""".strip(),
    )

    run_event_calls = cast(list[dict[str, object]], payload["runEventCalls"])
    assert payload["recoveryCalls"] == []
    assert [call["name"] for call in run_event_calls] == [
        "handleOutputDelta",
        "handleGenerationProgress",
    ]


def test_route_event_prunes_dedupe_state_after_terminal_run_event(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

routeEvent('text_delta', { text: 'first' }, {
    run_id: 'run-1',
    trace_id: 'run-1',
    event_id: 'evt-1',
});
routeEvent('text_delta', { text: 'duplicate' }, {
    run_id: 'run-1',
    trace_id: 'run-1',
    event_id: 'evt-1',
});
routeEvent('run_completed', {}, {
    run_id: 'run-1',
    trace_id: 'run-1',
    event_id: 'evt-2',
});
routeEvent('text_delta', { text: 'new lifecycle' }, {
    run_id: 'run-1',
    trace_id: 'run-1',
    event_id: 'evt-1',
});

await Promise.resolve();

console.log(JSON.stringify({
    runEventCalls: globalThis.__runEventCalls,
}));
""".strip(),
    )

    run_event_calls = cast(list[dict[str, object]], payload["runEventCalls"])
    assert [item["name"] for item in run_event_calls] == [
        "handleTextDelta",
        "handleRunCompleted",
        "handleTextDelta",
    ]
    first_args = cast(list[object], run_event_calls[0]["args"])
    last_args = cast(list[object], run_event_calls[2]["args"])
    assert first_args[0] == {"text": "first"}
    assert last_args[0] == {"text": "new lifecycle"}


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
            "delayMs": 350,
            "forceRefresh": True,
            "includeRounds": False,
            "quiet": True,
            "reason": "user_question_requested",
        }
    ]
    assert payload["tokenUsageCalls"] == []
    assert payload["runEventCalls"] == []


def test_route_event_applies_main_user_question_event_without_recovery_roundtrip(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');
const { state } = await import('./mockState.mjs');

state.activeRunId = 'run-1';

routeEvent(
    'user_question_requested',
    {
        question_id: 'question-1',
        questions: [{ question: 'Pick one', options: [{ label: 'Ship' }] }],
    },
    { run_id: 'run-1', trace_id: 'run-1', session_id: 'session-1' },
);
routeEvent(
    'user_question_answered',
    { question_id: 'question-1' },
    { run_id: 'run-1', trace_id: 'run-1', session_id: 'session-1' },
);

await Promise.resolve();

console.log(JSON.stringify({
    questionRequestedCalls: globalThis.__markUserQuestionRequestedCalls,
    questionAnsweredCalls: globalThis.__markUserQuestionAnsweredCalls,
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    runEventCalls: globalThis.__runEventCalls,
}));
""".strip(),
    )

    assert payload["questionRequestedCalls"] == [
        {
            "payload": {
                "question_id": "question-1",
                "questions": [{"question": "Pick one", "options": [{"label": "Ship"}]}],
            },
            "eventMeta": {
                "run_id": "run-1",
                "trace_id": "run-1",
                "session_id": "session-1",
            },
        }
    ]
    assert payload["questionAnsweredCalls"] == ["question-1"]
    assert payload["recoveryCalls"] == [
        {
            "sessionId": "session-1",
            "delayMs": 350,
            "forceRefresh": True,
            "includeRounds": False,
            "quiet": True,
            "reason": "user_question_requested",
        },
        {
            "sessionId": "session-1",
            "delayMs": 350,
            "forceRefresh": True,
            "includeRounds": False,
            "quiet": True,
            "reason": "user_question_answered",
        },
    ]
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
            "delayMs": 350,
            "forceRefresh": True,
            "includeRounds": False,
            "quiet": True,
            "reason": "llm_fallback_activated",
        },
        {
            "sessionId": "session-1",
            "delayMs": 350,
            "forceRefresh": True,
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


def test_route_event_ignores_foreground_command_background_task_events(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

routeEvent('background_task_started', {
    background_task_id: 'foreground-1',
    run_id: 'run-1',
    session_id: 'session-1',
    kind: 'command',
    execution_mode: 'foreground',
    command: 'python script.py',
    status: 'running',
}, { run_id: 'run-1', trace_id: 'run-1', session_id: 'session-1' });

await Promise.resolve();

console.log(JSON.stringify({
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    backgroundCalls: globalThis.__applyBackgroundTaskEventCalls,
    subagentCalls: globalThis.__normalModeSubagentEvents,
}));
""".strip(),
    )

    assert payload["recoveryCalls"] == []
    assert payload["backgroundCalls"] == []
    assert payload["subagentCalls"] == []


def test_route_event_updates_foreground_subagent_background_task_status(
    tmp_path: Path,
) -> None:
    payload = _run_event_router_script(
        tmp_path=tmp_path,
        runner_source="""
const { routeEvent } = await import('./eventRouterIndex.mjs');

routeEvent('background_task_stopped', {
    background_task_id: 'foreground-subagent-1',
    run_id: 'run-1',
    session_id: 'session-1',
    kind: 'subagent',
    execution_mode: 'foreground',
    subagent_run_id: 'subagent_run_deadbeef',
    subagent_instance_id: 'writer-1',
    subagent_role_id: 'writer',
    status: 'stopped',
}, { run_id: 'run-1', trace_id: 'run-1', session_id: 'session-1' });

await Promise.resolve();

console.log(JSON.stringify({
    recoveryCalls: globalThis.__scheduleRecoveryContinuityRefreshCalls,
    backgroundCalls: globalThis.__applyBackgroundTaskEventCalls,
    subagentCalls: globalThis.__normalModeSubagentEvents,
}));
""".strip(),
    )

    assert payload["recoveryCalls"] == []
    assert payload["backgroundCalls"] == []
    assert payload["subagentCalls"] == [
        {
            "sessionId": "session-1",
            "payload": {
                "background_task_id": "foreground-subagent-1",
                "run_id": "run-1",
                "session_id": "session-1",
                "kind": "subagent",
                "execution_mode": "foreground",
                "subagent_run_id": "subagent_run_deadbeef",
                "subagent_instance_id": "writer-1",
                "subagent_role_id": "writer",
                "status": "stopped",
            },
            "eventType": "background_task_stopped",
        }
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


def test_handle_subagent_run_terminal_resolves_active_child_session_by_run_id(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleSubagentRunTerminal } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
globalThis.__activeSubagentSession = {
    sessionId: 'session-1',
    instanceId: 'writer-1',
    roleId: 'writer',
    runId: 'subagent_run_deadbeef',
};

handleSubagentRunTerminal(
    '',
    'stopped',
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
    '',
);

console.log(JSON.stringify({
    statusCalls: globalThis.__updateNormalModeSubagentSessionStatusCalls,
    statusByRunCalls: globalThis.__updateNormalModeSubagentSessionStatusByRunIdCalls,
    settleCalls: globalThis.__settleActiveSubagentSessionAfterTerminalCalls,
}));
""".strip(),
    )

    assert payload["statusCalls"] == [
        {
            "sessionId": "session-1",
            "instanceId": "writer-1",
            "status": "stopped",
        }
    ]
    assert payload["statusByRunCalls"] == []
    assert payload["settleCalls"] == ["writer-1"]


def test_handle_subagent_run_active_resolves_active_child_session_by_run_id(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleSubagentRunActive } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
globalThis.__activeSubagentSession = {
    sessionId: 'session-1',
    instanceId: 'writer-1',
    roleId: 'writer',
    runId: 'subagent_run_deadbeef',
};

handleSubagentRunActive(
    '',
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
    '',
);

console.log(JSON.stringify({
    statusCalls: globalThis.__updateNormalModeSubagentSessionStatusCalls,
    statusByRunCalls: globalThis.__updateNormalModeSubagentSessionStatusByRunIdCalls,
}));
""".strip(),
    )

    assert payload["statusCalls"] == [
        {
            "sessionId": "session-1",
            "instanceId": "writer-1",
            "status": "running",
        }
    ]
    assert payload["statusByRunCalls"] == []


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


def test_primary_text_event_for_background_session_uses_overlay_not_current_dom(
    tmp_path: Path,
) -> None:
    payload = _run_run_events_script(
        tmp_path=tmp_path,
        runner_source="""
const { handleTextDelta } = await import('./runEvents.mjs');
const { state } = await import('./mockState.mjs');

state.currentSessionId = 'session-active';
state.currentSessionMode = 'normal';
state.mainAgentRoleId = 'MainAgent';
globalThis.__isCurrentRootEvent = false;

handleTextDelta(
    { text: 'background token' },
    {
        run_id: 'run-background',
        trace_id: 'run-background',
        session_id: 'session-background',
        event_id: 'event-1',
    },
    '',
    '',
);

console.log(JSON.stringify({
    appendCalls: globalThis.__appendStreamChunkCalls,
    overlayCalls: globalThis.__applyStreamOverlayEventCalls,
}));
""".strip(),
    )

    assert payload["appendCalls"] == []
    overlay_calls = payload["overlayCalls"]
    assert isinstance(overlay_calls, list)
    assert len(overlay_calls) == 1
    first_overlay_call = overlay_calls[0]
    assert isinstance(first_overlay_call, list)
    assert first_overlay_call[0] == "text_delta"
    overlay_meta = first_overlay_call[2]
    assert isinstance(overlay_meta, dict)
    assert overlay_meta["runId"] == "run-background"


def test_handle_model_step_finished_keeps_normal_mode_subagent_status_open(
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
    assert payload["statusCalls"] == []


def test_handle_model_step_finished_keeps_event_role_subagent_status_open(
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
globalThis.__activeSubagentSessionStreamContainer = {};

handleModelStepFinished(
    { run_id: 'subagent_run_deadbeef', trace_id: 'subagent_run_deadbeef' },
    'writer-1',
    'writer',
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
    assert payload["statusCalls"] == []


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
        "../../components/sessionSidebarStore.js": "./mockSessionSidebarStore.mjs",
        "./utils.js": "./mockUtils.mjs",
        "../api.js": "./mockApi.mjs",
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

export function applyBackgroundTaskEvent() {
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

export function getNormalModeSubagentSessionByRunId(sessionId, runId) {
    const active = globalThis.__activeSubagentSession || null;
    if (active && active.sessionId === sessionId && active.runId === runId) {
        return active;
    }
    return null;
}

export function clearNormalModeSubagentParentStopState(sessionId) {
    globalThis.__clearNormalModeSubagentParentStopStateCalls.push(sessionId);
}

export function markNormalModeSubagentSessionsRunningForParent(sessionId) {
    globalThis.__markNormalModeSubagentSessionsRunningForParentCalls.push(sessionId);
}

export function markNormalModeSubagentSessionsStoppedForParent(sessionId) {
    globalThis.__markNormalModeSubagentSessionsStoppedForParentCalls.push(sessionId);
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

export function updateNormalModeSubagentSessionStatusByRunId(sessionId, runId, status) {
    globalThis.__updateNormalModeSubagentSessionStatusByRunIdCalls.push({
        sessionId,
        runId,
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

export function applyStreamOverlayEvent(...args) {
    globalThis.__applyStreamOverlayEventCalls.push(args);
}

export function appendStreamChunk(...args) {
    globalThis.__appendStreamChunkCalls.push(args);
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

export function reconcileTerminalRunStreamState(runId) {
    globalThis.__reconcileTerminalRunStreamStateCalls.push(runId);
}

export function getCoordinatorStreamOverlay() {
    return null;
}

export function getRunTimelineSnapshot() {
    return null;
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
    (tmp_path / "mockSessionSidebarStore.mjs").write_text(
        """
export function markSidebarSessionRunActive(sessionId, detail = {}) {
    globalThis.__markSidebarSessionRunActiveCalls.push({ sessionId, detail });
}

export function markSidebarSessionRunTerminal(sessionId, detail = {}) {
    globalThis.__markSidebarSessionRunTerminalCalls.push({ sessionId, detail });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockUtils.mjs").write_text(
        """
export function coordinatorContainerFor() {
    return globalThis.__isCurrentRootEvent === false ? null : {};
}

export function isCurrentRootEvent() {
    return globalThis.__isCurrentRootEvent !== false;
}
    """.strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockApi.mjs").write_text(
        """
export async function markSessionTerminalRunViewed(sessionId) {
    globalThis.__markSessionTerminalRunViewedCalls.push(sessionId);
    if (Array.isArray(globalThis.__markSessionTerminalRunViewedResponses)) {
        const response = globalThis.__markSessionTerminalRunViewedResponses.shift();
        if (response?.errorStatus) {
            const error = new Error('busy');
            error.status = response.errorStatus;
            throw error;
        }
        return response || { status: 'ok' };
    }
    return { status: 'ok' };
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
globalThis.__updateNormalModeSubagentSessionStatusByRunIdCalls = [];
globalThis.__clearNormalModeSubagentParentStopStateCalls = [];
globalThis.__markNormalModeSubagentSessionsRunningForParentCalls = [];
globalThis.__markNormalModeSubagentSessionsStoppedForParentCalls = [];
globalThis.__finalizeStreamCalls = [];
globalThis.__reconcileTerminalRunStreamStateCalls = [];
globalThis.__settleActiveSubagentSessionAfterTerminalCalls = [];
globalThis.__sysLogCalls = [];
globalThis.__activeSubagentSession = null;
globalThis.__activeSubagentSessionStreamContainer = null;
globalThis.__markSessionTerminalRunViewedCalls = [];
globalThis.__markSessionTerminalRunViewedResponses = [];
globalThis.__markSidebarSessionRunActiveCalls = [];
globalThis.__markSidebarSessionRunTerminalCalls = [];
globalThis.__appendStreamChunkCalls = [];
globalThis.__applyStreamOverlayEventCalls = [];
globalThis.__documentDispatches = [];
globalThis.CustomEvent = class CustomEvent {{
    constructor(type, options = {{}}) {{
        this.type = type;
        this.detail = options.detail || null;
    }}
}};
globalThis.document = {{
    dispatchEvent(event) {{
        globalThis.__documentDispatches.push({{
            type: event.type,
            detail: event.detail,
        }});
        return true;
    }},
}};

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
        "../../components/rounds/timeline.js": "./mockRounds.mjs",
        "../../components/sessionTokenUsage.js": "./mockSessionTokenUsage.mjs",
        "../../components/subagentSessions.js": "./mockSubagentSessions.mjs",
        "../../components/messageRenderer.js": "./mockMessageRenderer.mjs",
        "../../components/runtimeInjectQueue.js": "./mockRuntimeInjectQueue.mjs",
        "../state.js": "./mockState.mjs",
        "../../utils/logger.js": "./mockLogger.mjs",
        "./runEvents.js": "./mockRunEvents.mjs",
        "./toolEvents.js": "./mockToolEvents.mjs",
        "./humanEvents.js": "./mockHumanEvents.mjs",
        "./notificationEvents.js": "./mockNotificationEvents.mjs",
        "./utils.js": "./mockUtils.mjs",
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

export function getRunPrimaryRoleId() {
    return 'MainAgent';
}

export function isRunPrimaryRoleId() {
    return true;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export function scheduleRecoveryContinuityRefresh(options) {
    globalThis.__scheduleRecoveryContinuityRefreshCalls.push(options);
}

export function applyBackgroundTaskEvent(payload, eventMeta = null, eventType = '') {
    globalThis.__applyBackgroundTaskEventCalls.push({ payload, eventMeta, eventType });
}

export function isDisplayableBackgroundTaskPayload(payload) {
    const executionMode = String(payload?.execution_mode || payload?.executionMode || '').trim();
    if (executionMode === 'foreground') {
        return false;
    }
    if (executionMode === 'background') {
        return true;
    }
    const kind = String(payload?.kind || '').trim();
    const subagentRunId = String(payload?.subagent_run_id || payload?.subagentRunId || '').trim();
    return kind === 'subagent' || subagentRunId.startsWith('subagent_run_');
}

export function markUserQuestionRequested(payload, eventMeta) {
    globalThis.__markUserQuestionRequestedCalls.push({ payload, eventMeta });
}

export function markUserQuestionAnswered(questionId) {
    globalThis.__markUserQuestionAnsweredCalls.push(questionId);
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
    (tmp_path / "mockSubagentSessions.mjs").write_text(
        """
export function rememberNormalModeSubagentFromBackgroundTask(sessionId, payload, eventType) {
    const kind = String(payload?.kind || '').trim();
    const subagentRunId = String(payload?.subagent_run_id || payload?.subagentRunId || '').trim();
    if (kind !== 'subagent' && !subagentRunId.startsWith('subagent_run_')) {
        return false;
    }
    globalThis.__normalModeSubagentEvents.push({ sessionId, payload, eventType });
    return true;
}

export function applySubagentSessionStatusEvent(payload, eventMeta = null) {
    globalThis.__subagentSessionStatusEvents.push({ payload, eventMeta });
    return true;
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
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function applyStreamOverlayEvent() {
    return undefined;
}

export function appendStreamInjectionMarker() {
    return undefined;
}

export function finalizeStream() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRuntimeInjectQueue.mjs").write_text(
        """
export function clearRuntimeInjectMessages() {
    return undefined;
}

export function removeRuntimeInjectMessage() {
    return undefined;
}

export function upsertRuntimeInjectMessage() {
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
    globalThis.__combinedEventCalls.push({ name, args });
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
export function handleSubagentRunActive(...args) { pushCall('handleSubagentRunActive', args); }
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
function pushCall(name, args) {
    globalThis.__toolEventCalls.push({ name, args });
    globalThis.__combinedEventCalls.push({ name, args });
}

export function handleToolApprovalRequested(...args) { pushCall('handleToolApprovalRequested', args); }
export function handleToolApprovalResolved(...args) { pushCall('handleToolApprovalResolved', args); }
export function handleToolCall(...args) { pushCall('handleToolCall', args); }
export function handleToolInputValidationFailed(...args) { pushCall('handleToolInputValidationFailed', args); }
export function handleToolResult(...args) { pushCall('handleToolResult', args); }
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
    (tmp_path / "mockUtils.mjs").write_text(
        """
export function coordinatorContainerFor() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
globalThis.__scheduleRecoveryContinuityRefreshCalls = [];
globalThis.__scheduleSessionTokenUsageRefreshCalls = [];
globalThis.__runEventCalls = [];
globalThis.__toolEventCalls = [];
globalThis.__combinedEventCalls = [];
globalThis.__normalModeSubagentEvents = [];
globalThis.__subagentSessionStatusEvents = [];
globalThis.__applyBackgroundTaskEventCalls = [];
globalThis.__markUserQuestionRequestedCalls = [];
globalThis.__markUserQuestionAnsweredCalls = [];

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
