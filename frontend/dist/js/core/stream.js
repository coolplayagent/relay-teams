/**
 * core/stream.js
 * Creates a run via HTTP, then subscribes to run events over SSE.
 */
import { sendUserPrompt, stopRun } from './api.js';
import { state } from './state.js';
import { els } from '../utils/dom.js';
import { markBackendOnline, refreshBackendStatus } from '../utils/backendStatus.js';
import {
    errorToPayload,
    logError,
    logInfo,
    logWarn,
    sysLog,
} from '../utils/logger.js';
import { routeEvent } from './eventRouter.js';
import { clearRunStreamState } from '../components/messageRenderer.js';

let pendingStopRequest = false;
let creatingRun = false;

export async function startIntentStream(promptText, sessionId, onCompleted, options = {}) {
    const approvalMode = options.approvalMode === 'standard' ? 'standard' : 'yolo';
    creatingRun = true;
    state.activeRunId = null;
    state.isGenerating = true;
    if (els.sendBtn) els.sendBtn.disabled = true;
    if (els.promptInput) els.promptInput.disabled = true;
    if (els.yoloModeToggle) els.yoloModeToggle.disabled = true;
    if (els.stopBtn) {
        els.stopBtn.style.display = 'inline-flex';
        els.stopBtn.disabled = false;
    }

    if (state.activeEventSource) {
        state.activeEventSource.close();
        state.activeEventSource = null;
    }

    let runId = null;
    try {
        const run = await sendUserPrompt(sessionId, promptText, approvalMode);
        runId = run.run_id;
        state.activeRunId = runId;
        logInfo('frontend.run.created', 'Frontend run created', {
            run_id: runId,
            session_id: sessionId,
            approval_mode: approvalMode,
        });
        if (typeof options.onRunCreated === 'function') {
            options.onRunCreated(run);
        }
    } catch (err) {
        creatingRun = false;
        pendingStopRequest = false;
        logError(
            'frontend.run.create_failed',
            err.message || 'Failed to create run',
            errorToPayload(err, { session_id: sessionId }),
        );
        sysLog(err.message || 'Failed to create run', 'log-error');
        endStream();
        return;
    }
    creatingRun = false;

    const shouldStopImmediately = pendingStopRequest;
    pendingStopRequest = false;
    if (shouldStopImmediately) {
        try {
            await stopRun(runId, { scope: 'main' });
            sysLog('Stop requested before stream attachment; stopped immediately after run creation.');
            await finalizeStopAndSyncRecovery(runId, sessionId);
            return;
        } catch (err) {
            sysLog(err.message || 'Failed to stop run', 'log-error');
        }
    }
    resumeRunStream(runId, sessionId, onCompleted, {
        reason: 'start',
        makeUiBusy: false,
    });
}

export function endStream() {
    creatingRun = false;
    pendingStopRequest = false;
    const finishedRunId = state.activeRunId;
    if (state.activeEventSource) {
        state.activeEventSource.close();
        state.activeEventSource = null;
    }
    if (finishedRunId) {
        clearRunStreamState(finishedRunId);
    }
    state.isGenerating = false;

    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.yoloModeToggle) {
        els.yoloModeToggle.disabled = false;
    }
    if (els.promptInput) {
        els.promptInput.disabled = false;
        els.promptInput.focus();
    }
}

export function resumeRunStream(runId, sessionId = state.currentSessionId, onCompleted = null, options = {}) {
    const safeRunId = typeof runId === 'string' ? runId.trim() : '';
    if (!safeRunId) return;

    const reason = typeof options.reason === 'string' && options.reason
        ? options.reason
        : 'resume';
    const makeUiBusy = options.makeUiBusy !== false;

    state.activeRunId = safeRunId;
    if (makeUiBusy) {
        state.isGenerating = true;
        if (els.sendBtn) els.sendBtn.disabled = true;
        if (els.promptInput) els.promptInput.disabled = true;
        if (els.stopBtn) {
            els.stopBtn.style.display = 'inline-flex';
            els.stopBtn.disabled = false;
        }
    }

    if (state.activeEventSource) {
        state.activeEventSource.close();
        state.activeEventSource = null;
    }
    clearRunStreamState(safeRunId);

    const url = `/api/runs/${safeRunId}/events`;
    logInfo('frontend.sse.opened', 'Run event stream opened', {
        run_id: safeRunId,
        reason,
        url,
    });
    sysLog(`SSE ${reason} run=${safeRunId}`);
    const es = new EventSource(url);
    state.activeEventSource = es;

    let done = false;
    const finish = () => {
        if (done) return;
        done = true;
        endStream();
        if (typeof onCompleted === 'function') {
            onCompleted(sessionId);
            return;
        }
        if (sessionId) {
            void refreshRoundsAfterCompletion(sessionId);
        }
    };

    es.onopen = () => {
        markBackendOnline();
    };

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.error) {
                logError('frontend.sse.payload_error', 'Run stream returned error', {
                    run_id: safeRunId,
                    error: data.error,
                });
                sysLog(`Run stream error: ${data.error}`, 'log-error');
                finish();
                return;
            }

            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');
            routeEvent(evType, payload, data);

            if (evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped') {
                logInfo('frontend.sse.terminal_event', 'Run stream reached terminal event', {
                    run_id: safeRunId,
                    event_type: evType,
                });
                finish();
            }
        } catch (e) {
            logError(
                'frontend.sse.parse_error',
                'SSE parse error',
                errorToPayload(e, { run_id: safeRunId, raw: event.data }),
            );
        }
    };

    es.onerror = () => {
        if (done) return;
        void refreshBackendStatus({ force: true });
        logWarn('frontend.sse.closed', 'Run event stream closed', {
            run_id: safeRunId,
        });
        sysLog('SSE closed.', 'log-error');
        finish();
    };
}

async function refreshRoundsAfterCompletion(sessionId) {
    if (!sessionId || state.currentSessionId !== sessionId) return;
    try {
        const recoveryModule = await import('../app/recovery.js');
        if (typeof recoveryModule.hydrateSessionView === 'function' && state.currentSessionId === sessionId) {
            await recoveryModule.hydrateSessionView(sessionId, { includeRounds: true, quiet: true });
        }
    } catch (e) {
        logError(
            'frontend.rounds.refresh_failed',
            'Failed to refresh rounds after stream completion',
            errorToPayload(e, { session_id: sessionId }),
        );
    }
}

export async function requestStopCurrentRun() {
    const activeRunId = String(state.activeRunId || '').trim();
    if (activeRunId) {
        await stopRun(activeRunId, { scope: 'main' });
        await finalizeStopAndSyncRecovery(activeRunId, state.currentSessionId);
        return true;
    }
    if (
        creatingRun
        || state.isGenerating
        || !!state.activeEventSource
        || !!els.promptInput?.disabled
        || !!els.sendBtn?.disabled
    ) {
        pendingStopRequest = true;
        sysLog('Stop requested. Waiting for run creation before sending stop.');
        return true;
    }
    return false;
}

async function syncRecoveryAfterStopRequest(runId, sessionId) {
    const safeRunId = String(runId || '').trim();
    const safeSessionId = String(sessionId || state.currentSessionId || '').trim();
    if (!safeRunId || !safeSessionId || state.currentSessionId !== safeSessionId) return;
    try {
        const recoveryModule = await import('../app/recovery.js');
        if (typeof recoveryModule.hydrateSessionView !== 'function') return;
        const snapshot = await recoveryModule.hydrateSessionView(safeSessionId, {
            includeRounds: true,
            quiet: true,
        });
        const activeRun = snapshot?.activeRun || null;
        if (!activeRun || activeRun.run_id !== safeRunId) return;

        const status = String(activeRun.status || '');
        const phase = String(activeRun.phase || '');
        const isRecoverable = activeRun.is_recoverable !== false;
        if (
            status === 'stopped'
            || phase === 'stopped'
            || status === 'completed'
            || status === 'failed'
            || !isRecoverable
        ) {
            endStream();
        }
    } catch (e) {
        logError(
            'frontend.recovery.sync_failed',
            'Failed to sync recovery after stop request',
            errorToPayload(e, { run_id: safeRunId, session_id: safeSessionId }),
        );
    }
}

async function finalizeStopAndSyncRecovery(runId, sessionId) {
    const safeRunId = String(runId || '').trim();
    const safeSessionId = String(sessionId || state.currentSessionId || '').trim();
    if (!safeRunId) return;

    endStream();
    await applyLocalStoppedSnapshot(safeRunId, safeSessionId);
    await syncRecoveryAfterStopRequest(safeRunId, safeSessionId);
}

async function applyLocalStoppedSnapshot(runId, sessionId) {
    const safeRunId = String(runId || '').trim();
    const safeSessionId = String(sessionId || state.currentSessionId || '').trim();
    if (!safeRunId) return;
    try {
        const recoveryModule = await import('../app/recovery.js');
        if (typeof recoveryModule.applyRecoverySnapshot === 'function') {
            recoveryModule.applyRecoverySnapshot({
                active_run: {
                    run_id: safeRunId,
                    status: 'stopped',
                    phase: 'stopped',
                    is_recoverable: true,
                    checkpoint_event_id: 0,
                    last_event_id: 0,
                    pending_tool_approval_count: 0,
                    stream_connected: false,
                    should_show_recover: true,
                },
                pending_tool_approvals: [],
                paused_subagent: null,
                round_snapshot: null,
            });
        }
        if (safeSessionId && typeof recoveryModule.scheduleRecoveryContinuityRefresh === 'function') {
            recoveryModule.scheduleRecoveryContinuityRefresh({
                sessionId: safeSessionId,
                delayMs: 0,
                includeRounds: true,
                quiet: true,
                reason: 'stop-sync',
            });
        }
    } catch (e) {
        logError(
            'frontend.recovery.apply_local_failed',
            'Failed to apply local stopped snapshot',
            errorToPayload(e, { run_id: safeRunId, session_id: safeSessionId }),
        );
    }
}
