/**
 * core/eventRouter/index.js
 * Event switchboard for SSE RunEventType payloads.
 */
import {
    applyBackgroundTaskEvent,
    isDisplayableBackgroundTaskPayload,
    scheduleRecoveryContinuityRefresh,
} from '../../app/recovery.js';
import {
    applySubagentSessionStatusEvent,
    rememberNormalModeSubagentFromBackgroundTask,
} from '../../components/subagentSessions.js';
import {
    appendStreamInjectionMarker,
    applyStreamOverlayEvent,
} from '../../components/messageRenderer.js';
import * as roundsTimeline from '../../components/rounds/timeline.js';
import {
    removeRuntimeInjectMessage,
    upsertRuntimeInjectMessage,
} from '../../components/runtimeInjectQueue.js';
import { scheduleSessionTokenUsageRefresh } from '../../components/sessionTokenUsage.js';
import {
    getRunPrimaryRoleId,
    isRunPrimaryRoleId,
    state,
} from '../state.js';
import { sysLog } from '../../utils/logger.js';
import {
    handleLlmFallbackActivated,
    handleLlmFallbackExhausted,
    handleLlmRetryExhausted,
    handleLlmRetryScheduled,
    handleModelStepFinished,
    handleModelStepStarted,
    handleOutputDelta,
    handleGenerationProgress,
    handleRunCompleted,
    handleRunFailed,
    handleRunStopped,
    handleRunStarted,
    handleSubagentRunActive,
    handleSubagentRunTerminal,
    handleThinkingDelta,
    handleThinkingFinished,
    handleThinkingStarted,
    handleTextDelta,
} from './runEvents.js';
import {
    handleToolApprovalRequested,
    handleToolApprovalResolved,
    handleToolCall,
    handleToolInputValidationFailed,
    handleToolResult,
} from './toolEvents.js';
import {
    handleGateResolved,
    handleSubagentResumed,
    handleSubagentStopped,
    handleSubagentGate,
} from './humanEvents.js';
import { handleNotificationRequested } from './notificationEvents.js';
import { coordinatorContainerFor } from './utils.js';

const BACKGROUND_TASK_UPDATE_REFRESH_DELAY_MS = 650;
const BACKGROUND_TASK_STATUS_REFRESH_DELAY_MS = 350;
const seenEventIdsByRun = new Map();
const MAX_SEEN_EVENT_IDS_PER_RUN = 2000;

export function routeEvent(evType, payload, eventMeta) {
    const eventRunId = String(eventMeta?.run_id || eventMeta?.trace_id || '').trim();
    if (isDuplicateRunEvent(eventRunId, eventMeta?.event_id)) {
        return;
    }
    const isSubagentRun = eventRunId.startsWith('subagent_run_');
    const backgroundTaskEvent = isBackgroundTaskEventType(evType);
    const displayableBackgroundTaskEvent = backgroundTaskEvent
        && isDisplayableBackgroundTaskPayload(payload);
    if (isSubagentRun && evType === 'token_usage') {
        scheduleSessionTokenUsageRefresh({ immediate: false });
    }
    if (
        isSubagentRun
        && (evType === 'user_question_requested' || evType === 'user_question_answered')
    ) {
        scheduleContinuityRefreshForEvent(evType);
    }
    if (!isSubagentRun) {
        if (eventMeta?.run_id) state.activeRunId = eventMeta.run_id;
        if (eventMeta?.trace_id && !state.activeRunId) state.activeRunId = eventMeta.trace_id;
        if (!backgroundTaskEvent || displayableBackgroundTaskEvent) {
            scheduleContinuityRefreshForEvent(evType);
        }
    }

    const instanceId = payload?.instance_id || eventMeta?.instance_id || null;
    const taskId = payload?.task_id || eventMeta?.task_id || null;
    const roleId = payload?.role_id || eventMeta?.role_id || null;
    if (instanceId && taskId) {
        if (!state.taskInstanceMap) state.taskInstanceMap = {};
        state.taskInstanceMap[taskId] = instanceId;
    }
    if (taskId) {
        if (!state.taskStatusMap) state.taskStatusMap = {};
        const byEvent = statusFromEventType(evType);
        if (byEvent) {
            state.taskStatusMap[taskId] = byEvent;
        } else if (evType === 'model_step_started') {
            state.taskStatusMap[taskId] = 'running';
        } else if (evType === 'model_step_finished' && state.taskStatusMap[taskId] === 'running') {
            state.taskStatusMap[taskId] = 'completed';
        }
    }
    if (evType === 'subagent_session_status_changed') {
        applySubagentSessionStatusEvent(payload, eventMeta);
        clearSeenRunEventsForTerminal(evType, eventRunId);
        return;
    }
    if (isSubagentRun) {
        if (evType === 'run_started' || evType === 'run_resumed') {
            handleSubagentRunActive(instanceId, eventMeta, roleId);
        } else if (evType === 'model_step_started') {
            handleModelStepStarted(eventMeta, instanceId, roleId);
        } else if (evType === 'text_delta') {
            handleTextDelta(payload, eventMeta, instanceId, roleId);
        } else if (evType === 'output_delta') {
            handleOutputDelta(payload, eventMeta, instanceId, roleId);
        } else if (evType === 'thinking_started') {
            handleThinkingStarted(payload, eventMeta, instanceId, roleId);
        } else if (evType === 'thinking_delta') {
            handleThinkingDelta(payload, eventMeta, instanceId, roleId);
        } else if (evType === 'thinking_finished') {
            handleThinkingFinished(payload, eventMeta, instanceId, roleId);
        } else if (evType === 'model_step_finished') {
            handleModelStepFinished(eventMeta, instanceId, roleId);
        } else if (evType === 'tool_call') {
            handleToolCall(payload, eventMeta, instanceId, roleId);
        } else if (evType === 'tool_input_validation_failed') {
            handleToolInputValidationFailed(payload, instanceId, eventMeta, roleId);
        } else if (evType === 'tool_result') {
            handleToolResult(payload, instanceId, eventMeta, roleId);
        } else if (evType === 'tool_approval_requested') {
            handleToolApprovalRequested(payload, eventMeta, instanceId);
        } else if (evType === 'tool_approval_resolved') {
            handleToolApprovalResolved(payload, instanceId, eventMeta, roleId);
        } else if (evType === 'run_completed') {
            handleSubagentRunTerminal(instanceId, 'completed', eventMeta, roleId);
        } else if (evType === 'run_failed') {
            handleSubagentRunTerminal(instanceId, 'failed', eventMeta, roleId);
        } else if (evType === 'run_stopped') {
            handleSubagentRunTerminal(instanceId, 'stopped', eventMeta, roleId);
        } else if (evType === 'injection_enqueued' || evType === 'injection_applied') {
            handleInjection(evType, payload, eventMeta);
        }
        clearSeenRunEventsForTerminal(evType, eventRunId);
        return;
    }
    if (evType === 'run_started') {
        handleRunStarted(eventMeta);
        roundsTimeline.syncRoundTodoVisibility?.();
    } else if (evType === 'run_resumed') {
        handleRunStarted(eventMeta, { resumeSubagents: true });
        roundsTimeline.syncRoundTodoVisibility?.();
    } else if (evType === 'model_step_started') {
        handleModelStepStarted(eventMeta, instanceId, roleId);
    } else if (evType === 'llm_retry_scheduled') {
        handleLlmRetryScheduled(payload, eventMeta);
    } else if (evType === 'llm_retry_exhausted') {
        handleLlmRetryExhausted(payload, eventMeta);
    } else if (evType === 'llm_fallback_activated') {
        handleLlmFallbackActivated(payload, eventMeta);
    } else if (evType === 'llm_fallback_exhausted') {
        handleLlmFallbackExhausted(payload, eventMeta);
    } else if (evType === 'text_delta') {
        handleTextDelta(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'output_delta') {
        handleOutputDelta(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'generation_progress') {
        handleGenerationProgress(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'thinking_started') {
        handleThinkingStarted(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'thinking_delta') {
        handleThinkingDelta(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'thinking_finished') {
        handleThinkingFinished(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'model_step_finished') {
        handleModelStepFinished(eventMeta, instanceId, roleId);
    } else if (evType === 'run_completed') {
        handleRunCompleted(eventMeta, payload);
        roundsTimeline.syncRoundTodoVisibility?.();
    } else if (evType === 'run_stopped') {
        handleRunStopped(eventMeta, payload);
        roundsTimeline.syncRoundTodoVisibility?.();
    } else if (evType === 'run_failed') {
        handleRunFailed(eventMeta, payload);
        roundsTimeline.syncRoundTodoVisibility?.();
    } else if (evType === 'tool_call') {
        handleToolCall(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'tool_input_validation_failed') {
        handleToolInputValidationFailed(payload, instanceId, eventMeta, roleId);
    } else if (evType === 'tool_result') {
        handleToolResult(payload, instanceId, eventMeta, roleId);
    } else if (evType === 'tool_approval_requested') {
        handleToolApprovalRequested(payload, eventMeta, instanceId);
    } else if (evType === 'tool_approval_resolved') {
        handleToolApprovalResolved(payload, instanceId, eventMeta, roleId);
    } else if (evType === 'injection_enqueued' || evType === 'injection_applied') {
        handleInjection(evType, payload, eventMeta);
    } else if (evType === 'user_question_requested' || evType === 'user_question_answered') {
        return;
    } else if (evType === 'notification_requested') {
        handleNotificationRequested(payload);
    } else if (evType === 'subagent_gate') {
        handleSubagentGate(payload);
    } else if (evType === 'subagent_stopped') {
        handleSubagentStopped(payload, eventMeta);
    } else if (evType === 'subagent_resumed') {
        handleSubagentResumed(payload, eventMeta);
    } else if (evType === 'gate_resolved') {
        handleGateResolved(payload, instanceId);
    } else if (backgroundTaskEvent) {
        handleBackgroundTaskEvent(evType, payload, eventMeta);
        return;
    } else if (evType === 'token_usage') {
        scheduleSessionTokenUsageRefresh({ immediate: false });
    } else if (evType === 'todo_updated') {
        roundsTimeline.updateRoundTodo?.(
            payload?.run_id || eventMeta?.run_id || eventMeta?.trace_id || '',
            payload,
        );
    } else {
        sysLog(`[evt] ${evType}`, 'log-info');
    }
    clearSeenRunEventsForTerminal(evType, eventRunId);
}

function handleInjection(evType, payload, eventMeta) {
    const runId = String(eventMeta?.run_id || eventMeta?.trace_id || payload?.run_id || '').trim();
    if (!runId || !payload || typeof payload !== 'object') {
        return;
    }
    if (payload.content_redacted === true || String(payload.visibility || 'public') !== 'public') {
        return;
    }
    const source = String(payload.source || 'user');
    if (source !== 'user' && source !== 'subagent') {
        return;
    }
    const projectedMessage = {
        ...payload,
        status: injectionStatus(evType, payload),
        mode: payload.delivery_mode || payload.mode || 'queued',
        recipient_instance_id: payload.recipient_instance_id || eventMeta?.instance_id || '',
        occurred_at: eventMeta?.occurred_at || payload.created_at || new Date().toISOString(),
        applied_at: evType === 'injection_applied'
            ? String(eventMeta?.occurred_at || new Date().toISOString())
            : String(payload.applied_at || ''),
    };
    if (evType === 'injection_applied') {
        const roleId = String(
            eventMeta?.role_id
            || payload.role_id
            || getRunPrimaryRoleId(runId)
            || '',
        ).trim();
        const recipient = String(
            payload.recipient_instance_id
            || eventMeta?.instance_id
            || 'primary',
        ).trim();
        const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
        const renderedLive = isPrimary && !state.activeSubagentSession
            ? appendStreamInjectionMarker(
                coordinatorContainerFor(eventMeta),
                recipient || 'primary',
                projectedMessage,
                {
                    runId,
                    roleId,
                    eventId: eventMeta?.event_id || projectedMessage.injection_id || '',
                },
            )
            : false;
        if (!renderedLive) {
            applyStreamOverlayEvent('injection_applied', projectedMessage, {
                runId,
                instanceId: recipient || 'primary',
                roleId,
                eventId: eventMeta?.event_id || projectedMessage.injection_id || '',
            });
        }
        removeRuntimeInjectMessage(runId, projectedMessage);
    } else {
        removeRuntimeInjectMessage(runId, projectedMessage, { render: false });
        upsertRuntimeInjectMessage(runId, projectedMessage);
    }
}

function injectionStatus(evType, payload) {
    if (evType === 'injection_applied') return 'applied';
    if (String(payload?.delivery_mode || payload?.mode || '') === 'interrupt') {
        return 'interrupting';
    }
    return 'queued';
}

function handleBackgroundTaskEvent(evType, payload, eventMeta) {
    const sessionId = String(
        payload?.session_id
        || payload?.sessionId
        || eventMeta?.session_id
        || eventMeta?.sessionId
        || state.currentSessionId
        || '',
    ).trim();
    if (!sessionId) {
        return;
    }
    rememberNormalModeSubagentFromBackgroundTask(sessionId, payload, evType);
    if (isDisplayableBackgroundTaskPayload(payload)) {
        applyBackgroundTaskEvent(payload, eventMeta, evType);
    }
}

function isDuplicateRunEvent(runId, eventId) {
    const safeRunId = String(runId || '').trim();
    const safeEventId = String(eventId || '').trim();
    if (!safeRunId || !safeEventId) return false;
    let seen = seenEventIdsByRun.get(safeRunId);
    if (!seen) {
        seen = new Set();
        seenEventIdsByRun.set(safeRunId, seen);
    }
    if (seen.has(safeEventId)) return true;
    seen.add(safeEventId);
    if (seen.size > MAX_SEEN_EVENT_IDS_PER_RUN) {
        const overflow = seen.size - MAX_SEEN_EVENT_IDS_PER_RUN;
        Array.from(seen).slice(0, overflow).forEach(id => seen.delete(id));
    }
    return false;
}

function clearSeenRunEventsForTerminal(evType, runId) {
    if (!isTerminalRunEvent(evType)) {
        return;
    }
    const safeRunId = String(runId || '').trim();
    if (safeRunId) {
        seenEventIdsByRun.delete(safeRunId);
    }
}

function isTerminalRunEvent(evType) {
    return evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped';
}

function isBackgroundTaskEventType(evType) {
    return (
        evType === 'background_task_started'
        || evType === 'background_task_updated'
        || evType === 'background_task_completed'
        || evType === 'background_task_stopped'
    );
}

function scheduleContinuityRefreshForEvent(evType) {
    const sessionId = state.currentSessionId;
    if (!sessionId) return;

    if (evType === 'run_started' || evType === 'run_resumed') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 300,
            forceRefresh: true,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (evType === 'tool_call') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 500,
            forceRefresh: true,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (evType === 'background_task_updated') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: BACKGROUND_TASK_UPDATE_REFRESH_DELAY_MS,
            forceRefresh: true,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (
        evType === 'llm_retry_scheduled'
        || evType === 'llm_retry_exhausted'
        || evType === 'llm_fallback_activated'
        || evType === 'llm_fallback_exhausted'
        || evType === 'tool_approval_requested'
        || evType === 'tool_approval_resolved'
        || evType === 'user_question_requested'
        || evType === 'user_question_answered'
        || evType === 'subagent_stopped'
        || evType === 'subagent_resumed'
        || evType === 'subagent_session_status_changed'
        || evType === 'notification_requested'
        || evType === 'gate_resolved'
    ) {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 350,
            forceRefresh: true,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (
        evType === 'background_task_started'
        || evType === 'background_task_completed'
        || evType === 'background_task_stopped'
    ) {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: BACKGROUND_TASK_STATUS_REFRESH_DELAY_MS,
            forceRefresh: true,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 650,
            forceRefresh: true,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
    }
}

function statusFromEventType(evType) {
    switch (evType) {
        case 'task_created':
            return 'created';
        case 'task_assigned':
            return 'assigned';
        case 'task_started':
            return 'running';
        case 'task_completed':
            return 'completed';
        case 'task_failed':
            return 'failed';
        case 'task_timeout':
            return 'timeout';
        case 'task_stopped':
            return 'stopped';
        default:
            return '';
    }
}
