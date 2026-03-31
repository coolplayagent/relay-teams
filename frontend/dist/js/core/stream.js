/**
 * core/stream.js
 * Creates a run via HTTP, then subscribes to run events over SSE.
 */
import {
    fetchSessionRecovery,
    fetchSessions,
    sendUserPrompt,
    stopRun,
} from './api.js';
import { refreshVisibleContextIndicators } from '../components/contextIndicators.js';
import { refreshSessionTopologyControls } from '../app/prompt.js';
import { scheduleSessionsRefresh } from '../components/sidebar.js';
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
import * as messageRenderer from '../components/messageRenderer.js';
import {
    getPrimaryRoleId,
    getPrimaryRoleLabel,
    getRunPrimaryRoleId,
    getRunPrimaryRoleLabel,
    setRunPrimaryRole,
    state,
} from './state.js';

let pendingStopRequest = false;
let creatingRun = false;
let activeStreamSessionId = '';
let activeConnection = null;
const backgroundStreams = new Map();
let backgroundDiscoveryTimer = null;
let backgroundDiscoveryPromise = null;
let backgroundDiscoveryQueued = false;
const unavailableSessionCooldownUntil = new Map();
const SESSION_NOT_FOUND_COOLDOWN_MS = 30000;
const unavailableRunCooldownUntil = new Map();
const RUN_NOT_FOUND_COOLDOWN_MS = 30000;
// Keep some room in the browser connection pool for control actions like stop.
const MAX_BACKGROUND_STREAMS = 2;

function setStreamUiBusy(isBusy, { focusPrompt = true } = {}) {
    state.isGenerating = isBusy;
    if (els.sendBtn) els.sendBtn.disabled = isBusy;
    if (els.promptInput) {
        els.promptInput.disabled = isBusy;
        if (!isBusy && focusPrompt) {
            els.promptInput.focus();
        }
    }
    if (els.yoloToggle) els.yoloToggle.disabled = isBusy;
    if (els.thinkingModeToggle) els.thinkingModeToggle.disabled = isBusy;
    if (els.thinkingEffortSelect) els.thinkingEffortSelect.disabled = isBusy;
    if (els.stopBtn) {
        els.stopBtn.style.display = isBusy ? 'inline-flex' : 'none';
        els.stopBtn.disabled = !isBusy;
    }
    refreshSessionTopologyControls();
    refreshVisibleContextIndicators({ immediate: true });
}

export async function startIntentStream(promptText, sessionId, onCompleted, options = {}) {
    const yolo = options.yolo === true;
    const thinking = options.thinking && typeof options.thinking === 'object'
        ? {
            enabled: options.thinking.enabled === true,
            effort: String(options.thinking.effort || 'medium'),
        }
        : { enabled: false, effort: null };
    creatingRun = true;
    state.activeRunId = null;
    setStreamUiBusy(true);

    releaseActiveStreamHandle();

    let runId = null;
    try {
        const run = await sendUserPrompt(
            sessionId,
            promptText,
            yolo,
            thinking,
            options.targetRoleId || null,
        );
        runId = run.run_id;
        clearRunUnavailableCooldown(runId);
        state.activeRunId = runId;
        setRunPrimaryRole(runId, run.target_role_id || options.targetRoleId || getPrimaryRoleId());
        logInfo('frontend.run.created', 'Frontend run created', {
            run_id: runId,
            session_id: sessionId,
            yolo,
            thinking_enabled: thinking.enabled,
            thinking_effort: thinking.effort,
            target_role_id: run.target_role_id || options.targetRoleId || null,
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
    attachRunStream(runId, sessionId, onCompleted, {
        reason: 'start',
        makeUiBusy: false,
    });
}

export function endStream(options = {}) {
    creatingRun = false;
    pendingStopRequest = false;
    const preserveRunStreamState = options.preserveRunStreamState === true;
    const focusPrompt = options.focusPrompt !== false;
    const finishedRunId = releaseActiveStreamHandle({ close: true });
    if (finishedRunId && !preserveRunStreamState) {
        messageRenderer.clearRunStreamState(finishedRunId);
    }
    setStreamUiBusy(false, { focusPrompt });
}

export function detachActiveStreamForSessionSwitch(options = {}) {
    if (!activeConnection?.runId || !activeConnection?.eventSource) {
        endStream({
            preserveRunStreamState: true,
            focusPrompt: options.focusPrompt !== false,
        });
        return false;
    }

    const connection = activeConnection;
    connection.mode = 'background';
    connection.onCompleted = null;
    connection.sessionId = String(
        activeStreamSessionId || state.currentSessionId || connection.sessionId || '',
    ).trim();
    backgroundStreams.set(connection.runId, connection);
    activeConnection = null;
    state.activeEventSource = null;
    activeStreamSessionId = '';
    setStreamUiBusy(false, { focusPrompt: options.focusPrompt !== false });
    ensureBackgroundDiscoveryLoop();
    return true;
}

export function attachRunStream(runId, sessionId = state.currentSessionId, onCompleted = null, options = {}) {
    const safeRunId = typeof runId === 'string' ? runId.trim() : '';
    if (!safeRunId) return;

    const reason = typeof options.reason === 'string' && options.reason
        ? options.reason
        : 'resume';
    const makeUiBusy = options.makeUiBusy !== false;
    const afterEventId = typeof options.afterEventId === 'number' && options.afterEventId >= 0
        ? options.afterEventId
        : null;
    const ignoreUnavailable = options.ignoreUnavailable === true;
    if (!ignoreUnavailable && isRunUnavailable(safeRunId)) {
        return;
    }
    const sameRunAlreadyStreaming = !!(
        state.activeEventSource
        && state.activeRunId === safeRunId
        && state.isGenerating
    );
    if (sameRunAlreadyStreaming) {
        return;
    }

    const backgroundConnection = backgroundStreams.get(safeRunId);
    if (backgroundConnection && backgroundConnection.eventSource) {
        releaseActiveStreamHandle({ close: true });
        promoteBackgroundStream(backgroundConnection, {
            sessionId,
            onCompleted,
            makeUiBusy,
        });
        return;
    }

    state.activeRunId = safeRunId;
    if (makeUiBusy) {
        setStreamUiBusy(true);
    }

    releaseActiveStreamHandle({ close: true });

    const connection = {
        runId: safeRunId,
        sessionId: String(sessionId || state.currentSessionId || '').trim(),
        eventSource: null,
        onCompleted,
        mode: 'active',
        lastEventId: afterEventId !== null ? afterEventId : 0,
        primaryRoleId: String(getPrimaryRoleId() || '').trim(),
        primaryLabel: String(getRunPrimaryRoleLabel(safeRunId) || 'Main Agent'),
        closed: false,
        terminal: false,
        reconnectTimer: null,
    };
    connection.primaryRoleId = String(getRunPrimaryRoleId(safeRunId) || connection.primaryRoleId || '').trim();
    openRunStreamConnection(connection, {
        reason,
        afterEventId,
    });
}

export function syncBackgroundStreamsForSessions(sessionRecords = []) {
    void reconcileBackgroundStreams(sessionRecords);
}

export function resumeRunStream(runId, sessionId = state.currentSessionId, onCompleted = null, options = {}) {
    attachRunStream(runId, sessionId, onCompleted, options);
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

function releaseActiveStreamHandle(options = {}) {
    const activeRunId = String(state.activeRunId || '').trim();
    if (activeConnection) {
        clearReconnectTimer(activeConnection);
        if (activeConnection.eventSource && options.close !== false) {
            activeConnection.eventSource.close();
        }
        if (options.close !== false) {
            activeConnection.eventSource = null;
            activeConnection.closed = true;
        }
        activeConnection = null;
    } else if (state.activeEventSource && options.close !== false) {
        state.activeEventSource.close();
    }
    state.activeEventSource = null;
    activeStreamSessionId = '';
    return activeRunId;
}

function promoteBackgroundStream(connection, options = {}) {
    if (!connection || !connection.eventSource) {
        return;
    }
    backgroundStreams.delete(connection.runId);
    connection.mode = 'active';
    connection.sessionId = String(options.sessionId || state.currentSessionId || connection.sessionId || '').trim();
    connection.onCompleted = options.onCompleted || null;
    connection.primaryRoleId = String(getRunPrimaryRoleId(connection.runId) || connection.primaryRoleId || '').trim();
    connection.primaryLabel = String(getRunPrimaryRoleLabel(connection.runId) || connection.primaryLabel || 'Main Agent');
    activeConnection = connection;
    state.activeRunId = connection.runId;
    state.activeEventSource = connection.eventSource;
    activeStreamSessionId = connection.sessionId;
    if (options.makeUiBusy !== false) {
        setStreamUiBusy(true);
    }
    ensureBackgroundDiscoveryLoop();
}

function openRunStreamConnection(connection, { reason, afterEventId = null } = {}) {
    const urlParams = afterEventId !== null ? `?after_event_id=${afterEventId}` : '';
    const url = `/api/runs/${connection.runId}/events${urlParams}`;
    logInfo('frontend.sse.opened', 'Run event stream opened', {
        run_id: connection.runId,
        reason,
        url,
        mode: connection.mode,
    });
    sysLog(`SSE ${reason} run=${connection.runId}`);
    const es = new EventSource(url);
    connection.eventSource = es;
    connection.closed = false;
    connection.terminal = false;
    clearReconnectTimer(connection);

    if (connection.mode === 'active') {
        activeConnection = connection;
        state.activeEventSource = es;
        state.activeRunId = connection.runId;
        activeStreamSessionId = connection.sessionId;
    } else {
        backgroundStreams.set(connection.runId, connection);
    }
    ensureBackgroundDiscoveryLoop();

    es.onopen = () => {
        markBackendOnline();
    };

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.error) {
                if (isRunNotFoundError(data.error)) {
                    markRunUnavailable(connection.runId);
                }
                logError('frontend.sse.payload_error', 'Run stream returned error', {
                    run_id: connection.runId,
                    error: data.error,
                    mode: connection.mode,
                });
                sysLog(`Run stream error: ${data.error}`, 'log-error');
                if (connection.mode === 'active') {
                    finishActiveConnection(connection);
                } else {
                    finishBackgroundConnection(connection);
                }
                return;
            }

            const eventId = Number(data.event_id || 0);
            if (eventId > 0) {
                connection.lastEventId = Math.max(connection.lastEventId, eventId);
            }

            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');
            if (connection.mode === 'background') {
                applyBackgroundRunEvent(connection, evType, payload, data);
            } else {
                routeEvent(evType, payload, data);
            }

            if (isTerminalRunEvent(evType)) {
                logInfo('frontend.sse.terminal_event', 'Run stream reached terminal event', {
                    run_id: connection.runId,
                    event_type: evType,
                    mode: connection.mode,
                });
                connection.terminal = true;
                if (connection.mode === 'active') {
                    finishActiveConnection(connection);
                } else {
                    finishBackgroundConnection(connection);
                }
            }
        } catch (e) {
            logError(
                'frontend.sse.parse_error',
                'SSE parse error',
                errorToPayload(e, { run_id: connection.runId, raw: event.data, mode: connection.mode }),
            );
        }
    };

    es.onerror = () => {
        if (connection.closed || connection.terminal) return;
        void refreshBackendStatus({ force: true });
        logWarn('frontend.sse.closed', 'Run event stream closed', {
            run_id: connection.runId,
            mode: connection.mode,
        });
        sysLog('SSE closed.', 'log-error');
        if (connection.mode === 'active') {
            finishActiveConnection(connection, { preserveRunStreamState: true });
            return;
        }
        backgroundStreams.set(connection.runId, connection);
        if (connection.eventSource) {
            connection.eventSource.close();
            connection.eventSource = null;
        }
        scheduleBackgroundReconnect(connection);
    };
}

function finishActiveConnection(connection, finishOptions = {}) {
    if (!connection || connection.closed) return;
    connection.closed = true;
    clearReconnectTimer(connection);
    if (connection.eventSource) {
        connection.eventSource.close();
        connection.eventSource = null;
    }
    if (activeConnection === connection) {
        activeConnection = null;
    }
    if (state.activeEventSource) {
        state.activeEventSource = null;
    }
    if (String(state.activeRunId || '').trim() === connection.runId) {
        activeStreamSessionId = '';
    }
    endStream(finishOptions);
    if (typeof connection.onCompleted === 'function') {
        connection.onCompleted(connection.sessionId);
        return;
    }
    if (connection.sessionId) {
        void refreshRoundsAfterCompletion(connection.sessionId);
    }
    ensureBackgroundDiscoveryLoop();
}

function finishBackgroundConnection(connection) {
    if (!connection || connection.closed) return;
    connection.closed = true;
    clearReconnectTimer(connection);
    if (connection.eventSource) {
        connection.eventSource.close();
        connection.eventSource = null;
    }
    backgroundStreams.delete(connection.runId);
    scheduleSessionsRefresh();
    ensureBackgroundDiscoveryLoop();
}

function scheduleBackgroundReconnect(connection) {
    if (!connection || connection.closed || connection.terminal) return;
    clearReconnectTimer(connection);
    connection.reconnectTimer = setTimeout(() => {
        connection.reconnectTimer = null;
        if (connection.closed || connection.terminal || connection.mode !== 'background') {
            return;
        }
        openRunStreamConnection(connection, {
            reason: 'background-reconnect',
            afterEventId: connection.lastEventId > 0 ? connection.lastEventId : null,
        });
    }, 1000);
}

function clearReconnectTimer(connection) {
    if (!connection?.reconnectTimer) return;
    clearTimeout(connection.reconnectTimer);
    connection.reconnectTimer = null;
}

function applyBackgroundRunEvent(connection, evType, payload, eventMeta) {
    const instanceId = payload?.instance_id || eventMeta?.instance_id || null;
    const roleId = payload?.role_id || eventMeta?.role_id || null;
    const primaryRoleId = String(connection.primaryRoleId || '').trim();
    const isPrimary = !roleId || (primaryRoleId && String(roleId).trim() === primaryRoleId);
    const label = isPrimary ? connection.primaryLabel : (roleId || 'Agent');
    const streamInstanceId = isPrimary ? 'primary' : instanceId;

    if (typeof messageRenderer.applyStreamOverlayEvent === 'function') {
        messageRenderer.applyStreamOverlayEvent(evType, payload, {
            runId: connection.runId,
            instanceId: streamInstanceId,
            roleId: isPrimary ? primaryRoleId : roleId,
            label,
            cleanupDelayMs: 4000,
        });
    }
    if (isTerminalRunEvent(evType)) {
        scheduleSessionsRefresh();
    }
}

function isTerminalRunEvent(evType) {
    return evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped';
}

function ensureBackgroundDiscoveryLoop() {
    if (!shouldRunBackgroundDiscovery()) {
        if (backgroundDiscoveryTimer) {
            clearTimeout(backgroundDiscoveryTimer);
            backgroundDiscoveryTimer = null;
        }
        return;
    }
    if (backgroundDiscoveryTimer || backgroundDiscoveryPromise) {
        return;
    }
    backgroundDiscoveryTimer = setTimeout(() => {
        backgroundDiscoveryTimer = null;
        void runBackgroundDiscovery();
    }, 2500);
}

function shouldRunBackgroundDiscovery() {
    return !!(
        activeConnection
        || backgroundStreams.size > 0
        || state.currentSessionId
    );
}

async function runBackgroundDiscovery() {
    if (backgroundDiscoveryPromise) {
        backgroundDiscoveryQueued = true;
        return;
    }
    backgroundDiscoveryPromise = (async () => {
        try {
            const sessions = await fetchSessions();
            await reconcileBackgroundStreams(sessions);
        } catch (e) {
            logWarn('frontend.background.discovery_failed', 'Failed to discover running sessions', {
                error: e?.message || String(e),
            });
        } finally {
            backgroundDiscoveryPromise = null;
            if (backgroundDiscoveryQueued) {
                backgroundDiscoveryQueued = false;
                void runBackgroundDiscovery();
                return;
            }
            ensureBackgroundDiscoveryLoop();
        }
    })();
    await backgroundDiscoveryPromise;
}

async function reconcileBackgroundStreams(sessionRecords = []) {
    const records = Array.isArray(sessionRecords) ? sessionRecords : [];
    const activeRunIds = new Set();
    if (activeConnection?.runId) {
        activeRunIds.add(activeConnection.runId);
    }
    backgroundStreams.forEach(connection => {
        if (connection?.runId) {
            activeRunIds.add(connection.runId);
        }
    });

    const candidates = [];
    for (const rawRecord of records) {
        const record = rawRecord && typeof rawRecord === 'object' ? rawRecord : null;
        const sessionId = String(record?.session_id || '').trim();
        const runId = String(record?.active_run_id || '').trim();
        const status = String(record?.active_run_status || '').trim();
        if (!sessionId || !runId) {
            continue;
        }
        if (isSessionUnavailable(sessionId)) {
            continue;
        }
        if (isRunUnavailable(runId)) {
            continue;
        }
        if (status !== 'running' && status !== 'queued') {
            continue;
        }
        candidates.push(record);
    }

    candidates.sort((left, right) => backgroundRecordTimestamp(right) - backgroundRecordTimestamp(left));

    const desiredRunIds = new Set();
    for (const record of candidates) {
        const runId = String(record?.active_run_id || '').trim();
        if (!runId) {
            continue;
        }
        if (desiredRunIds.size >= MAX_BACKGROUND_STREAMS) {
            break;
        }
        desiredRunIds.add(runId);
        if (activeRunIds.has(runId)) {
            continue;
        }
        await attachBackgroundStreamForSession(record);
        activeRunIds.add(runId);
    }

    Array.from(backgroundStreams.values()).forEach(connection => {
        if (!connection?.runId) {
            return;
        }
        if (desiredRunIds.has(connection.runId)) {
            return;
        }
        finishBackgroundConnection(connection);
    });
}

function backgroundRecordTimestamp(record) {
    const raw = String(record?.updated_at || record?.updatedAt || '').trim();
    if (!raw) {
        return 0;
    }
    const parsed = Date.parse(raw);
    return Number.isNaN(parsed) ? 0 : parsed;
}

async function attachBackgroundStreamForSession(record) {
    const sessionId = String(record?.session_id || '').trim();
    const runId = String(record?.active_run_id || '').trim();
    if (!sessionId || !runId) {
        return false;
    }
    if (isSessionUnavailable(sessionId)) {
        return false;
    }
    if (isRunUnavailable(runId)) {
        return false;
    }
    if (backgroundStreams.has(runId)) {
        return true;
    }
    if (activeConnection?.runId === runId) {
        return true;
    }

    try {
        const snapshot = await fetchSessionRecovery(sessionId);
        const activeRun = snapshot?.active_run && typeof snapshot.active_run === 'object'
            ? snapshot.active_run
            : snapshot?.activeRun && typeof snapshot.activeRun === 'object'
                ? snapshot.activeRun
                : null;
        clearSessionUnavailableCooldown(sessionId);
        const recoveredRunId = String(activeRun?.run_id || '').trim();
        const recoveredStatus = String(activeRun?.status || '').trim();
        if (!recoveredRunId || recoveredRunId !== runId) {
            return false;
        }
        if (recoveredStatus !== 'running' && recoveredStatus !== 'queued') {
            return false;
        }
        const recoveredPrimaryRoleId = String(
            activeRun?.primary_role_id
            || snapshot?.round_snapshot?.primary_role_id
            || snapshot?.roundSnapshot?.primary_role_id
            || '',
        ).trim();
        setRunPrimaryRole(runId, recoveredPrimaryRoleId || null);
        const sessionMode = String(record?.session_mode || 'normal').trim().toLowerCase();
        const afterEventId = resolveBackgroundAfterEventId(activeRun);
        const connection = {
            runId,
            sessionId,
            eventSource: null,
            onCompleted: null,
            mode: 'background',
            lastEventId: afterEventId,
            primaryRoleId: String(
                recoveredPrimaryRoleId || getRunPrimaryRoleId(runId, sessionMode) || getPrimaryRoleId(sessionMode) || ''
            ).trim(),
            primaryLabel: String(getRunPrimaryRoleLabel(runId, sessionMode) || getPrimaryRoleLabel(sessionMode) || 'Main Agent'),
            closed: false,
            terminal: false,
            reconnectTimer: null,
        };
        openRunStreamConnection(connection, {
            reason: 'background-discovery',
            afterEventId,
        });
        return true;
    } catch (e) {
        if (e?.status === 404) {
            markSessionUnavailable(sessionId);
        }
        logWarn('frontend.background.attach_failed', 'Failed to attach background stream', {
            session_id: sessionId,
            run_id: runId,
            error: e?.message || String(e),
        });
        return false;
    }
}

function resolveBackgroundAfterEventId(activeRun) {
    if (!activeRun || typeof activeRun !== 'object') {
        return 0;
    }
    const lastEventId = Number(activeRun.last_event_id || 0);
    if (lastEventId > 0) {
        return lastEventId;
    }
    const checkpointEventId = Number(activeRun.checkpoint_event_id || 0);
    if (checkpointEventId > 0) {
        return checkpointEventId;
    }
    return 0;
}

function isRunNotFoundError(errorMessage) {
    const safe = String(errorMessage || '').toLowerCase();
    return safe.includes('not found') && safe.includes('run');
}

function markRunUnavailable(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    unavailableRunCooldownUntil.set(
        safeRunId,
        Date.now() + RUN_NOT_FOUND_COOLDOWN_MS,
    );
}

function clearRunUnavailableCooldown(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    unavailableRunCooldownUntil.delete(safeRunId);
}

function isRunUnavailable(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return false;
    }
    const cooldownUntil = unavailableRunCooldownUntil.get(safeRunId);
    if (typeof cooldownUntil !== 'number') {
        return false;
    }
    if (cooldownUntil > Date.now()) {
        return true;
    }
    unavailableRunCooldownUntil.delete(safeRunId);
    return false;
}

function markSessionUnavailable(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    unavailableSessionCooldownUntil.set(
        safeSessionId,
        Date.now() + SESSION_NOT_FOUND_COOLDOWN_MS,
    );
}

function clearSessionUnavailableCooldown(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    unavailableSessionCooldownUntil.delete(safeSessionId);
}

function isSessionUnavailable(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return false;
    }
    const cooldownUntil = unavailableSessionCooldownUntil.get(safeSessionId) || 0;
    if (cooldownUntil > Date.now()) {
        return true;
    }
    unavailableSessionCooldownUntil.delete(safeSessionId);
    return false;
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
                    status: 'stopping',
                    phase: 'stopping',
                    is_recoverable: false,
                    checkpoint_event_id: 0,
                    last_event_id: 0,
                    pending_tool_approval_count: 0,
                    stream_connected: false,
                    should_show_recover: false,
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
