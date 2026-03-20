/**
 * app/retryStatus.js
 * Drive the live retry countdown on the round timeline.
 */
import {
    appendRoundRetryEvent,
    removeRoundRetryEvent,
    updateRoundRetryEvent,
} from '../components/rounds.js';

let retryState = null;
const RETRY_PHASE_SCHEDULED = 'scheduled';
const RETRY_PHASE_RUNNING = 'retrying';
const RETRY_PHASE_FAILED = 'failed';

export function showLlmRetryStatus(payload = {}, eventMeta = {}) {
    if (retryState?.runId && retryState?.eventId) {
        removeRoundRetryEvent(retryState.runId, retryState.eventId);
    }
    const retryInMs = Math.max(0, Number(payload?.retry_in_ms || 0));
    const runId = String(eventMeta?.run_id || payload?.run_id || '').trim();
    const attemptNumber = Number(payload?.attempt_number || 0);
    const occurredAt = String(eventMeta?.occurred_at || new Date().toISOString()).trim();
    const eventId = `retry-${runId || 'run'}-${attemptNumber}-${Date.now()}`;
    retryState = {
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
    };
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

export function beginLlmRetryAttempt() {
    if (!retryState?.runId || !retryState?.eventId) return;
    updateRoundRetryEvent(retryState.runId, retryState.eventId, {
        remaining_ms: null,
        is_active: false,
        phase: RETRY_PHASE_RUNNING,
    });
}

export function markLlmRetrySucceeded() {
    clearLlmRetryStatus();
}

export function markLlmRetryFailed(errorMessage = '') {
    if (!retryState?.runId || !retryState?.eventId) return;
    updateRoundRetryEvent(retryState.runId, retryState.eventId, {
        remaining_ms: null,
        is_active: false,
        phase: RETRY_PHASE_FAILED,
        error_message: String(errorMessage || retryState.errorMessage || '').trim(),
    });
    retryState = null;
}

export function clearLlmRetryStatus() {
    if (retryState?.runId && retryState?.eventId) {
        removeRoundRetryEvent(retryState.runId, retryState.eventId);
    }
    retryState = null;
}
