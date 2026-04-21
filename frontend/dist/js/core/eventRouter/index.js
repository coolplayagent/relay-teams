/**
 * core/eventRouter/index.js
 * Event switchboard for SSE RunEventType payloads.
 */
import { scheduleRecoveryContinuityRefresh } from '../../app/recovery.js';
import { syncRoundTodoVisibility, updateRoundTodo } from '../../components/rounds.js';
import { scheduleSessionTokenUsageRefresh } from '../../components/sessionTokenUsage.js';
import { state } from '../state.js';
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

const BACKGROUND_TASK_UPDATE_REFRESH_DELAY_MS = 250;

export function routeEvent(evType, payload, eventMeta) {
    const eventRunId = String(eventMeta?.run_id || eventMeta?.trace_id || '').trim();
    const isSubagentRun = eventRunId.startsWith('subagent_run_');
    if (isSubagentRun && evType === 'token_usage') {
        scheduleSessionTokenUsageRefresh({ immediate: true });
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
        scheduleContinuityRefreshForEvent(evType);
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
    if (isSubagentRun) {
        if (evType === 'model_step_started') {
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
            handleModelStepFinished(eventMeta, instanceId);
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
        }
        return;
    }
    if (evType === 'run_started') {
        handleRunStarted(eventMeta);
        syncRoundTodoVisibility();
    } else if (evType === 'run_resumed') {
        handleRunStarted(eventMeta);
        syncRoundTodoVisibility();
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
        handleModelStepFinished(eventMeta, instanceId);
    } else if (evType === 'run_completed') {
        handleRunCompleted(eventMeta);
        syncRoundTodoVisibility();
    } else if (evType === 'run_stopped') {
        handleRunStopped(eventMeta, payload);
        syncRoundTodoVisibility();
    } else if (evType === 'run_failed') {
        handleRunFailed(eventMeta, payload);
        syncRoundTodoVisibility();
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
    } else if (evType === 'user_question_requested' || evType === 'user_question_answered') {
        return;
    } else if (evType === 'notification_requested') {
        handleNotificationRequested(payload);
    } else if (evType === 'subagent_gate') {
        handleSubagentGate(payload);
    } else if (evType === 'subagent_stopped') {
        handleSubagentStopped(payload);
    } else if (evType === 'subagent_resumed') {
        handleSubagentResumed(payload);
    } else if (evType === 'gate_resolved') {
        handleGateResolved(payload, instanceId);
    } else if (
        evType === 'background_task_started'
        || evType === 'background_task_updated'
        || evType === 'background_task_completed'
        || evType === 'background_task_stopped'
    ) {
        return;
    } else if (evType === 'token_usage') {
        scheduleSessionTokenUsageRefresh({ immediate: true });
    } else if (evType === 'todo_updated') {
        updateRoundTodo(
            payload?.run_id || eventMeta?.run_id || eventMeta?.trace_id || '',
            payload,
        );
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

    if (evType === 'background_task_updated') {
        scheduleRecoveryContinuityRefresh({
            sessionId,
            delayMs: BACKGROUND_TASK_UPDATE_REFRESH_DELAY_MS,
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
        || evType === 'tool_result'
        || evType === 'output_delta'
        || evType === 'generation_progress'
        || evType === 'tool_approval_requested'
        || evType === 'tool_approval_resolved'
        || evType === 'user_question_requested'
        || evType === 'user_question_answered'
        || evType === 'subagent_stopped'
        || evType === 'subagent_resumed'
        || evType === 'background_task_started'
        || evType === 'background_task_completed'
        || evType === 'background_task_stopped'
        || evType === 'notification_requested'
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
