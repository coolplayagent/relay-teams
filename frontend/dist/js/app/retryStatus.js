/**
 * app/retryStatus.js
 * Drive the live retry countdown on the round timeline.
 */
import {
    appendRoundRetryEvent,
    removeRoundRetryEvent,
    updateRoundRetryEvent,
} from '../components/rounds/timeline.js';

const RETRY_PHASE_SCHEDULED = 'scheduled';
const RETRY_PHASE_RUNNING = 'retrying';
const RETRY_PHASE_FAILED = 'failed';
const RETRY_PHASE_SUCCEEDED = 'succeeded';
const RETRY_SUCCESS_CLEAR_DELAY_MS = 900;

const retryStatesByRun = new Map();
let lastRetryRunId = '';

export function showLlmRetryStatus(payload = {}, eventMeta = {}) {
    const runId = String(eventMeta?.run_id || eventMeta?.trace_id || payload?.run_id || '').trim();
    if (!runId) {
        return;
    }
    const previousState = retryStatesByRun.get(runId);
    if (previousState?.eventId) {
        removeRoundRetryEvent(runId, previousState.eventId);
        clearRetryStateTimer(previousState);
    }
    const retryInMs = Math.max(0, Number(payload?.retry_in_ms || 0));
    const attemptNumber = Number(payload?.attempt_number || 0);
    const occurredAt = String(eventMeta?.occurred_at || new Date().toISOString()).trim();
    const eventId = `retry-${runId || 'run'}-${attemptNumber}-${Date.now()}`;
    const retryState = {
        eventId,
        runId,
        roleId: payload?.role_id || eventMeta?.role_id || '',
        instanceId: payload?.instance_id || eventMeta?.instance_id || '',
        attemptNumber,
        totalAttempts: Number(payload?.total_attempts || 0),
        retryInMs,
        errorCode: String(payload?.error_code || '').trim(),
        errorMessage: String(payload?.error_message || '').trim(),
        occurredAt,
        clearTimerId: 0,
    };
    retryStatesByRun.set(runId, retryState);
    lastRetryRunId = runId;
    appendRoundRetryEvent(retryState.runId, {
        event_id: retryState.eventId,
        occurred_at: retryState.occurredAt,
        role_id: retryState.roleId,
        instance_id: retryState.instanceId,
        attempt_number: retryState.attemptNumber,
        total_attempts: retryState.totalAttempts,
        retry_in_ms: retryState.retryInMs,
        is_active: true,
        phase: RETRY_PHASE_SCHEDULED,
        error_code: retryState.errorCode,
        error_message: retryState.errorMessage,
    });
}

export function beginLlmRetryAttempt(runId = '') {
    const retryState = resolveRetryState(runId);
    if (!retryState?.runId || !retryState?.eventId) return;
    clearRetryStateTimer(retryState);
    updateRoundRetryEvent(retryState.runId, retryState.eventId, {
        remaining_ms: null,
        is_active: true,
        phase: RETRY_PHASE_RUNNING,
    });
}

export function markLlmRetrySucceeded(runId = '') {
    const retryState = resolveRetryState(runId);
    if (!retryState?.runId || !retryState?.eventId) return;
    clearRetryStateTimer(retryState);
    updateRoundRetryEvent(retryState.runId, retryState.eventId, {
        remaining_ms: null,
        is_active: false,
        phase: RETRY_PHASE_SUCCEEDED,
    });
    retryState.clearTimerId = setTimeout(() => {
        clearLlmRetryStatus(retryState.runId);
    }, RETRY_SUCCESS_CLEAR_DELAY_MS);
}

export function markLlmRetryFailed(errorMessage = '', runId = '') {
    const retryState = resolveRetryState(runId);
    if (!retryState?.runId || !retryState?.eventId) return;
    clearRetryStateTimer(retryState);
    updateRoundRetryEvent(retryState.runId, retryState.eventId, {
        remaining_ms: null,
        is_active: false,
        phase: RETRY_PHASE_FAILED,
        error_message: String(errorMessage || retryState.errorMessage || '').trim(),
    });
    retryStatesByRun.delete(retryState.runId);
    if (lastRetryRunId === retryState.runId) {
        lastRetryRunId = '';
    }
}

export function clearLlmRetryStatus(runId = '') {
    const retryState = resolveRetryState(runId);
    if (retryState?.runId && retryState?.eventId) {
        clearRetryStateTimer(retryState);
        removeRoundRetryEvent(retryState.runId, retryState.eventId);
        retryStatesByRun.delete(retryState.runId);
        if (lastRetryRunId === retryState.runId) {
            lastRetryRunId = '';
        }
    }
}

function resolveRetryState(runId = '') {
    const safeRunId = String(runId || '').trim();
    if (safeRunId) {
        return retryStatesByRun.get(safeRunId) || null;
    }
    if (lastRetryRunId && retryStatesByRun.has(lastRetryRunId)) {
        return retryStatesByRun.get(lastRetryRunId) || null;
    }
    const first = retryStatesByRun.values().next();
    return first.done ? null : first.value;
}

function clearRetryStateTimer(retryState) {
    if (!retryState?.clearTimerId) {
        return;
    }
    clearTimeout(retryState.clearTimerId);
    retryState.clearTimerId = 0;
}
