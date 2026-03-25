/**
 * core/eventRouter/index.js
 * Event switchboard for SSE RunEventType payloads.
 */
import { scheduleRecoveryContinuityRefresh } from '../../app/recovery.js';
import { scheduleSessionTokenUsageRefresh } from '../../components/sessionTokenUsage.js';
import { state } from '../state.js';
import { sysLog } from '../../utils/logger.js';
import {
    handleLlmRetryExhausted,
    handleLlmRetryScheduled,
    handleModelStepFinished,
    handleModelStepStarted,
    handleRunCompleted,
    handleRunFailed,
    handleRunStopped,
    handleRunStarted,
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
    handleAwaitingHumanDispatch,
    handleGateResolved,
    handleHumanTaskDispatched,
    handleSubagentResumed,
    handleSubagentStopped,
    handleSubagentGate,
} from './humanEvents.js';
import { handleNotificationRequested } from './notificationEvents.js';

export function routeEvent(evType, payload, eventMeta) {
    if (eventMeta?.run_id) state.activeRunId = eventMeta.run_id;
    if (eventMeta?.trace_id && !state.activeRunId) state.activeRunId = eventMeta.trace_id;

    scheduleContinuityRefreshForEvent(evType);

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
    if (evType === 'run_started') {
        handleRunStarted(eventMeta);
    } else if (evType === 'run_resumed') {
        handleRunStarted(eventMeta);
    } else if (evType === 'model_step_started') {
        handleModelStepStarted(eventMeta, instanceId, roleId);
    } else if (evType === 'llm_retry_scheduled') {
        handleLlmRetryScheduled(payload, eventMeta);
    } else if (evType === 'llm_retry_exhausted') {
        handleLlmRetryExhausted(payload, eventMeta);
    } else if (evType === 'text_delta') {
        handleTextDelta(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'thinking_started') {
        handleThinkingStarted(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'thinking_delta') {
        handleThinkingDelta(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'thinking_finished') {
        handleThinkingFinished(payload, eventMeta, instanceId, roleId);
    } else if (evType === 'model_step_finished') {
        handleModelStepFinished(eventMeta, instanceId);
    } else if (evType === 'run_completed') {
        handleRunCompleted(eventMeta);
    } else if (evType === 'run_stopped') {
        handleRunStopped(eventMeta, payload);
    } else if (evType === 'run_failed') {
        handleRunFailed(eventMeta, payload);
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
    } else if (evType === 'notification_requested') {
        handleNotificationRequested(payload);
    } else if (evType === 'awaiting_human_dispatch') {
        handleAwaitingHumanDispatch(payload);
    } else if (evType === 'human_task_dispatched') {
        handleHumanTaskDispatched(payload);
    } else if (evType === 'subagent_gate') {
        handleSubagentGate(payload);
    } else if (evType === 'subagent_stopped') {
        handleSubagentStopped(payload);
    } else if (evType === 'subagent_resumed') {
        handleSubagentResumed(payload);
    } else if (evType === 'gate_resolved') {
        handleGateResolved(payload, instanceId);
    } else if (evType === 'token_usage') {
        scheduleSessionTokenUsageRefresh({ immediate: true });
    } else {
        sysLog(`[evt] ${evType}`, 'log-info');
    }
}

function scheduleContinuityRefreshForEvent(evType) {
    const sessionId = state.currentSessionId;
    if (!sessionId) return;

    if (evType === 'run_started' || evType === 'run_resumed') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 0,
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
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (
        evType === 'llm_retry_scheduled'
        || evType === 'llm_retry_exhausted'
        || evType === 'tool_result'
        || evType === 'tool_approval_requested'
        || evType === 'tool_approval_resolved'
        || evType === 'subagent_stopped'
        || evType === 'subagent_resumed'
        || evType === 'notification_requested'
        || evType === 'awaiting_human_dispatch'
        || evType === 'human_task_dispatched'
        || evType === 'gate_resolved'
    ) {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 0,
            includeRounds: false,
            quiet: true,
            reason: evType,
        });
        return;
    }

    if (evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: 0,
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
