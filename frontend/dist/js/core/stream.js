/**
 * core/stream.js
 * Creates a run via HTTP, then subscribes to run events over SSE.
 */
import {
    fetchSessionSubagents,
    fetchSessions,
    sendUserPrompt,
    stopRun,
} from './api.js';
import { refreshVisibleContextIndicators } from '../components/contextIndicators.js';
import { renderRuntimeInjectQueue } from '../components/runtimeInjectQueue.js';
import {
    markNormalModeSubagentSessionsStoppedForParent,
} from '../components/subagentSessions.js';
import { refreshSessionTopologyControls } from '../app/prompt.js';
import { scheduleSessionsRefresh } from '../components/sidebar.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
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
let pendingRunStart = null;
let runStartTokenSeed = 0;
let activeStreamSessionId = '';
let activeConnection = null;
const backgroundStreams = new Map();
const pendingBackgroundAttachRunIds = new Set();
let multiplexEventSource = null;
let multiplexReconnectTimer = null;
let multiplexConnectionSignature = '';
let multiplexReconnectDelayMs = 900;
let multiplexSyncSuspendDepth = 0;
let multiplexSyncPendingReason = '';
const runStreamLastEventIds = new Map();
const runStreamAttention = new Map();
let runStreamAttentionSequence = 0;
const normalModeSubagentStreams = new Map();
let normalModeSubagentConnection = null;
let backgroundDiscoveryTimer = null;
let backgroundDiscoveryPromise = null;
let backgroundDiscoveryQueued = false;
let backgroundDiscoveryPausedUntil = 0;
let foregroundNavigationStreamToken = 0;
let normalModeSubagentDiscoveryTimer = null;
let normalModeSubagentDiscoveryPromise = null;
let normalModeSubagentDiscoveryQueued = false;
const unavailableSessionCooldownUntil = new Map();
const SESSION_NOT_FOUND_COOLDOWN_MS = 30000;
const unavailableRunCooldownUntil = new Map();
const RUN_NOT_FOUND_COOLDOWN_MS = 30000;
const MAX_MULTIPLEX_RUN_STREAMS = 32;
const MULTIPLEX_RECONNECT_MAX_DELAY_MS = 5000;
const FOREGROUND_NAVIGATION_STREAM_PAUSE_MS = 1200;
const NORMAL_MODE_SUBAGENT_DISCOVERY_DELAY_MS = 2500;
const NORMAL_MODE_SUBAGENT_RECONNECT_DELAY_MS = 900;
const RUN_CREATED_SIDEBAR_REFRESH_DELAY_MS = 360;

function setStreamUiBusy(isBusy, { focusPrompt = true } = {}) {
    state.isGenerating = isBusy;
    const runtimeInjectEnabled = isBusy && !!String(state.activeRunId || '').trim();
    renderRuntimeInjectQueue(runtimeInjectEnabled ? state.activeRunId : '');
    if (els.sendBtn) {
        els.sendBtn.disabled = isBusy && !runtimeInjectEnabled;
        if (els.sendBtn.style) els.sendBtn.style.display = '';
        els.sendBtn.title = t('composer.send_title');
        els.sendBtn.setAttribute('aria-label', t('composer.send_title'));
    }
    if (els.promptInput) {
        if (!els.promptInput.dataset.idlePlaceholder) {
            els.promptInput.dataset.idlePlaceholder = els.promptInput.getAttribute('placeholder') || '';
        }
        els.promptInput.disabled = isBusy && !runtimeInjectEnabled;
        els.promptInput.placeholder = runtimeInjectEnabled
            ? t('inject.queue.placeholder')
            : els.promptInput.dataset.idlePlaceholder;
        if ((!isBusy || runtimeInjectEnabled) && focusPrompt) {
            els.promptInput.focus();
        }
    }
    if (els.yoloToggle) els.yoloToggle.disabled = isBusy;
    if (els.shellSafetyPolicyToggle) {
        els.shellSafetyPolicyToggle.disabled = isBusy;
    }
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
    const safeSessionId = String(sessionId || '').trim();
    const yolo = options.yolo === true;
    const thinking = options.thinking && typeof options.thinking === 'object'
        ? {
            enabled: options.thinking.enabled === true,
            effort: String(options.thinking.effort || 'medium'),
        }
        : { enabled: false, effort: null };
    if (options.detached === true) {
        await startDetachedIntentStream(promptText, safeSessionId, {
            yolo,
            thinking,
            targetRoleId: options.targetRoleId || null,
            inputParts: Array.isArray(options.inputParts) ? options.inputParts : null,
            skills: Array.isArray(options.skills) ? options.skills : null,
            displayInputParts: Array.isArray(options.displayInputParts)
                ? options.displayInputParts
                : null,
        });
        return;
    }

    const runStart = {
        token: ++runStartTokenSeed,
        sessionId: safeSessionId,
        runId: '',
        detached: false,
    };
    pendingRunStart = runStart;
    creatingRun = true;
    state.activeRunId = null;
    setStreamUiBusy(true);

    releaseActiveStreamHandle();

    let runId = null;
    try {
        const run = await sendUserPrompt(
            safeSessionId,
            promptText,
            yolo,
            thinking,
            options.targetRoleId || null,
            Array.isArray(options.inputParts) ? options.inputParts : null,
            Array.isArray(options.skills) ? options.skills : null,
            Array.isArray(options.displayInputParts)
                ? options.displayInputParts
                : null,
        );
        runId = run.run_id;
        runStart.runId = runId;
        clearRunUnavailableCooldown(runId);
        setRunPrimaryRole(runId, run.target_role_id || options.targetRoleId || getPrimaryRoleId());
        logInfo('frontend.run.created', 'Frontend run created', {
            run_id: runId,
            session_id: safeSessionId,
            yolo,
            thinking_enabled: thinking.enabled,
            thinking_effort: thinking.effort,
            target_role_id: run.target_role_id || options.targetRoleId || null,
        });
        if (shouldKeepCreatedRunInBackground(runStart)) {
            finishPendingRunStart(runStart, {
                clearStopRequest: true,
                releaseUi: false,
            });
            scheduleSessionsRefresh(RUN_CREATED_SIDEBAR_REFRESH_DELAY_MS, { forceRefresh: true });
            attachRunStreamAsBackground(runId, safeSessionId, {
                reason: 'start-background',
            });
            return;
        }
        state.activeRunId = runId;
        setStreamUiBusy(true, { focusPrompt: false });
        if (typeof options.onRunCreated === 'function') {
            options.onRunCreated(run);
        }
        scheduleSessionsRefresh(RUN_CREATED_SIDEBAR_REFRESH_DELAY_MS, { forceRefresh: true });
    } catch (err) {
        logError(
            'frontend.run.create_failed',
            err.message || 'Failed to create run',
            errorToPayload(err, { session_id: safeSessionId, detached: runStart.detached }),
        );
        if (shouldKeepCreatedRunInBackground(runStart)) {
            finishPendingRunStart(runStart, {
                clearStopRequest: true,
                releaseUi: false,
            });
            scheduleSessionsRefresh(RUN_CREATED_SIDEBAR_REFRESH_DELAY_MS, { forceRefresh: true });
            return;
        }
        finishPendingRunStart(runStart, {
            clearStopRequest: true,
            releaseUi: false,
        });
        sysLog(err.message || 'Failed to create run', 'log-error');
        endStream();
        return;
    }
    finishPendingRunStart(runStart);

    const shouldStopImmediately = pendingStopRequest;
    pendingStopRequest = false;
    if (shouldStopImmediately) {
        try {
            await stopRun(runId, { scope: 'main' });
            sysLog('Stop requested before stream attachment; stopped immediately after run creation.');
            await finalizeStopAndSyncRecovery(runId, safeSessionId);
            return;
        } catch (err) {
            sysLog(err.message || 'Failed to stop run', 'log-error');
        }
    }
    attachRunStream(runId, safeSessionId, onCompleted, {
        reason: 'start',
        makeUiBusy: false,
    });
}

async function startDetachedIntentStream(promptText, sessionId, options) {
    try {
        const run = await sendUserPrompt(
            sessionId,
            promptText,
            options.yolo === true,
            options.thinking,
            options.targetRoleId || null,
            options.inputParts,
            options.skills,
            options.displayInputParts,
        );
        const runId = String(run?.run_id || '').trim();
        if (!runId) {
            throw new Error('Run creation did not return a run id.');
        }
        clearRunUnavailableCooldown(runId);
        setRunPrimaryRole(
            runId,
            run.target_role_id || options.targetRoleId || getPrimaryRoleId(),
        );
        logInfo('frontend.run.created', 'Frontend background run created', {
            run_id: runId,
            session_id: sessionId,
            yolo: options.yolo === true,
            thinking_enabled: options.thinking?.enabled === true,
            thinking_effort: options.thinking?.effort || null,
            target_role_id: run.target_role_id || options.targetRoleId || null,
        });
        scheduleSessionsRefresh(RUN_CREATED_SIDEBAR_REFRESH_DELAY_MS, {
            forceRefresh: true,
        });
        attachRunStreamAsBackground(runId, sessionId, {
            reason: 'start-background',
        });
    } catch (err) {
        logError(
            'frontend.run.create_failed',
            err.message || 'Failed to create background run',
            errorToPayload(err, { session_id: sessionId, detached: true }),
        );
        scheduleSessionsRefresh(RUN_CREATED_SIDEBAR_REFRESH_DELAY_MS, {
            forceRefresh: true,
        });
    }
}

export function endStream(options = {}) {
    creatingRun = false;
    pendingStopRequest = false;
    pendingRunStart = null;
    const preserveRunStreamState = options.preserveRunStreamState === true;
    const focusPrompt = options.focusPrompt !== false;
    const finishedRunId = releaseActiveStreamHandle({ close: true });
    if (finishedRunId && !preserveRunStreamState) {
        messageRenderer.clearRunStreamState(finishedRunId);
    }
    setStreamUiBusy(false, { focusPrompt });
}

function finishPendingRunStart(runStart, options = {}) {
    if (pendingRunStart !== runStart) {
        return;
    }
    pendingRunStart = null;
    creatingRun = false;
    if (options.clearStopRequest === true) {
        pendingStopRequest = false;
    }
    if (options.releaseUi === true) {
        setStreamUiBusy(false, { focusPrompt: options.focusPrompt !== false });
    }
}

function shouldKeepCreatedRunInBackground(runStart) {
    if (!runStart) {
        return false;
    }
    if (runStart.detached) {
        return true;
    }
    if (pendingRunStart !== runStart) {
        return true;
    }
    const currentSessionId = String(state.currentSessionId || '').trim();
    return !!(runStart.sessionId && currentSessionId && currentSessionId !== runStart.sessionId);
}

function detachPendingRunStartForSessionSwitch(options = {}) {
    if (!pendingRunStart) {
        return false;
    }
    pendingRunStart.detached = true;
    creatingRun = false;
    pendingStopRequest = false;
    const pendingRunId = String(pendingRunStart.runId || '').trim();
    const activeRunId = String(state.activeRunId || '').trim();
    if (!pendingRunId || !activeRunId || activeRunId === pendingRunId) {
        state.activeRunId = null;
    }
    setStreamUiBusy(false, { focusPrompt: options.focusPrompt !== false });
    ensureBackgroundDiscoveryLoop();
    return true;
}

export function detachActiveStreamForSessionSwitch(options = {}) {
    if (detachPendingRunStartForSessionSwitch(options)) {
        return true;
    }
    if (!activeConnection?.runId) {
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
    touchRunStreamAttention(connection.runId);
    backgroundStreams.set(connection.runId, connection);
    activeConnection = null;
    state.activeEventSource = null;
    activeStreamSessionId = '';
    setStreamUiBusy(false, { focusPrompt: options.focusPrompt !== false });
    ensureBackgroundDiscoveryLoop();
    return true;
}

export function prepareStreamsForForegroundNavigation(sessionId = '') {
    const targetSessionId = String(sessionId || '').trim();
    foregroundNavigationStreamToken += 1;
    backgroundDiscoveryPausedUntil = Math.max(
        backgroundDiscoveryPausedUntil,
        Date.now() + FOREGROUND_NAVIGATION_STREAM_PAUSE_MS,
    );
    rescheduleBackgroundDiscoveryAfterPause();
    const targetBackground = Array.from(backgroundStreams.values()).find(connection => (
        targetSessionId
        && connection?.sessionId === targetSessionId
        && !connection.closed
        && !connection.terminal
    ));
    if (targetBackground) {
        touchRunStreamAttention(targetBackground.runId);
    }
    enforceMultiplexRunBudget(targetBackground?.runId || '');
    requestMultiplexRunConnection('foreground-navigation');

    if (
        normalModeSubagentConnection
        && (!targetSessionId || normalModeSubagentConnection.sessionId !== targetSessionId)
    ) {
        finishNormalModeSubagentSessionConnection(normalModeSubagentConnection);
    }
    Array.from(normalModeSubagentStreams.values()).forEach(connection => {
        if (targetSessionId && connection?.sessionId === targetSessionId) {
            return;
        }
        finishNormalModeSubagentConnection(connection);
    });
}

export function hasPendingRunCreation(sessionId = '') {
    if (!pendingRunStart) {
        return false;
    }
    const safeSessionId = String(sessionId || '').trim();
    return !safeSessionId || pendingRunStart.sessionId === safeSessionId;
}

export function detachNormalModeSubagentStreamsForSessionSwitch(sessionId = '') {
    const safeSessionId = String(sessionId || '').trim();
    if (
        normalModeSubagentConnection
        && (!safeSessionId || normalModeSubagentConnection.sessionId === safeSessionId)
    ) {
        finishNormalModeSubagentSessionConnection(normalModeSubagentConnection);
    }
    Array.from(normalModeSubagentStreams.values()).forEach(connection => {
        if (!connection) {
            return;
        }
        if (safeSessionId && connection.sessionId !== safeSessionId) {
            return;
        }
        finishNormalModeSubagentConnection(connection);
    });
    clearNormalModeSubagentDiscoveryTimer();
}

export function closeNormalModeSubagentStream(runId = '') {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return false;
    }
    if (normalModeSubagentConnection?.desiredRunIds?.has(safeRunId)) {
        normalModeSubagentConnection.desiredRunIds.delete(safeRunId);
        if (
            normalModeSubagentConnection.desiredRunIds.size === 0
            && !state.activeSubagentSession
        ) {
            finishNormalModeSubagentSessionConnection(normalModeSubagentConnection);
        }
        return true;
    }
    const connection = normalModeSubagentStreams.get(safeRunId) || null;
    if (!connection) {
        return false;
    }
    finishNormalModeSubagentConnection(connection);
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
        activeConnection
        && activeConnection.runId === safeRunId
        && state.activeRunId === safeRunId
        && state.isGenerating
    );
    if (sameRunAlreadyStreaming) {
        return;
    }

    const backgroundConnection = backgroundStreams.get(safeRunId);
    if (backgroundConnection && !backgroundConnection.closed) {
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

function attachRunStreamAsBackground(runId, sessionId, options = {}) {
    const safeRunId = String(runId || '').trim();
    const safeSessionId = String(sessionId || '').trim();
    if (!safeRunId || !safeSessionId) {
        return false;
    }
    if (isRunUnavailable(safeRunId) || isSessionUnavailable(safeSessionId)) {
        return false;
    }
    if (
        backgroundStreams.has(safeRunId)
        || pendingBackgroundAttachRunIds.has(safeRunId)
        || activeConnection?.runId === safeRunId
    ) {
        return true;
    }
    const reason = typeof options.reason === 'string' && options.reason
        ? options.reason
        : 'background';
    const connection = {
        runId: safeRunId,
        sessionId: safeSessionId,
        eventSource: null,
        onCompleted: null,
        mode: 'background',
        lastEventId: 0,
        primaryRoleId: String(getRunPrimaryRoleId(safeRunId) || getPrimaryRoleId() || '').trim(),
        primaryLabel: String(getRunPrimaryRoleLabel(safeRunId) || getPrimaryRoleLabel() || 'Main Agent'),
        closed: false,
        terminal: false,
        reconnectTimer: null,
    };
    openRunStreamConnection(connection, { reason });
    return true;
}

export function syncBackgroundStreamsForSessions(sessionRecords = []) {
    void reconcileBackgroundStreams(sessionRecords);
}

export function syncNormalModeSubagentStreams(sessionId, subagentRecords = []) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId || safeSessionId !== String(state.currentSessionId || '').trim()) {
        return;
    }
    if (String(state.currentSessionMode || '').trim().toLowerCase() !== 'normal') {
        detachNormalModeSubagentStreamsForSessionSwitch(safeSessionId);
        return;
    }
    const rows = Array.isArray(subagentRecords) ? subagentRecords : [];
    const desiredRunIds = desiredNormalModeSubagentRunIds(rows);
    if (desiredRunIds.size === 0) {
        if (!state.activeSubagentSession && normalModeSubagentConnection) {
            finishNormalModeSubagentSessionConnection(normalModeSubagentConnection);
        }
        ensureNormalModeSubagentDiscoveryLoop();
        return;
    }
    if (
        !normalModeSubagentConnection
        || normalModeSubagentConnection.closed
        || normalModeSubagentConnection.sessionId !== safeSessionId
    ) {
        if (normalModeSubagentConnection) {
            finishNormalModeSubagentSessionConnection(normalModeSubagentConnection);
        }
        openNormalModeSubagentSessionStream(safeSessionId, rows, desiredRunIds);
    } else {
        normalModeSubagentConnection.desiredRunIds = desiredRunIds;
        normalModeSubagentConnection.lastEventId = Math.max(
            normalModeSubagentConnection.lastEventId || 0,
            resolveSubagentSessionAfterEventId(rows),
        );
    }
    ensureNormalModeSubagentDiscoveryLoop();
}

export function scheduleCurrentSessionSubagentDiscovery(options = {}) {
    const delayMs = Number(options?.delayMs ?? NORMAL_MODE_SUBAGENT_DISCOVERY_DELAY_MS);
    if (normalModeSubagentDiscoveryPromise) {
        normalModeSubagentDiscoveryQueued = true;
        return;
    }
    if (normalModeSubagentDiscoveryTimer) {
        return;
    }
    normalModeSubagentDiscoveryTimer = setTimeout(() => {
        normalModeSubagentDiscoveryTimer = null;
        void runNormalModeSubagentDiscovery();
    }, Math.max(0, delayMs));
}

export function resumeRunStream(runId, sessionId = state.currentSessionId, onCompleted = null, options = {}) {
    attachRunStream(runId, sessionId, onCompleted, options);
}

async function refreshRoundsAfterCompletion(sessionId, runId = '', options = {}) {
    if (!sessionId || state.currentSessionId !== sessionId) return;
    try {
        const shouldHydrate = options.hydrate !== false;
        if (shouldHydrate) {
            const recoveryModule = await import('../app/recovery.js');
            if (typeof recoveryModule.hydrateSessionView === 'function' && state.currentSessionId === sessionId) {
                await recoveryModule.hydrateSessionView(sessionId, {
                    includeRounds: true,
                    forceRefresh: true,
                    quiet: true,
                    roundsScrollPolicy: 'completion-auto',
                });
            }
        }
        const safeRunId = String(runId || '').trim();
        if (safeRunId && state.currentSessionId === sessionId) {
            const timelineModule = await import('../components/rounds/timeline.js');
            if (typeof timelineModule.refreshTerminalRoundFromHistory === 'function') {
                await timelineModule.refreshTerminalRoundFromHistory(sessionId, safeRunId, {
                    scrollPolicy: 'completion-auto',
                    navigatorLayoutReason: 'terminal-history',
                    expectedToolCallIds: options.expectedToolCallIds,
                });
            }
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
        if (options.close !== false) {
            activeConnection.eventSource = null;
            activeConnection.closed = true;
        }
        activeConnection = null;
    }
    state.activeEventSource = null;
    activeStreamSessionId = '';
    requestMultiplexRunConnection('release-active');
    return activeRunId;
}

function promoteBackgroundStream(connection, options = {}) {
    if (!connection || connection.closed || connection.terminal) {
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
    connection.eventSource = multiplexEventSource;
    state.activeEventSource = multiplexEventSource;
    activeStreamSessionId = connection.sessionId;
    touchRunStreamAttention(connection.runId);
    if (options.makeUiBusy !== false) {
        setStreamUiBusy(true);
    }
    requestMultiplexRunConnection('promote-background');
    ensureBackgroundDiscoveryLoop();
}

function openRunStreamConnection(connection, { reason, afterEventId = null } = {}) {
    const resolvedAfterEventId = afterEventId !== null
        ? afterEventId
        : Number(runStreamLastEventIds.get(connection.runId) || connection.lastEventId || 0);
    connection.lastEventId = Math.max(
        Number(connection.lastEventId || 0),
        Number(resolvedAfterEventId || 0),
    );
    if (connection.lastEventId > 0) {
        runStreamLastEventIds.set(connection.runId, connection.lastEventId);
    }
    connection.eventSource = multiplexEventSource;
    connection.closed = false;
    connection.terminal = false;
    clearReconnectTimer(connection);
    touchRunStreamAttention(connection.runId);

    if (connection.mode === 'active') {
        activeConnection = connection;
        state.activeRunId = connection.runId;
        state.activeEventSource = multiplexEventSource;
        activeStreamSessionId = connection.sessionId;
    } else {
        backgroundStreams.set(connection.runId, connection);
    }
    enforceMultiplexRunBudget(connection.runId);
    requestMultiplexRunConnection(reason || connection.mode || 'run');
    ensureBackgroundDiscoveryLoop();
    if (connection.mode === 'active') {
        ensureNormalModeSubagentDiscoveryLoop();
    }
}

function requestMultiplexRunConnection(reason = 'sync') {
    if (multiplexSyncSuspendDepth > 0) {
        multiplexSyncPendingReason = multiplexSyncPendingReason || reason;
        return;
    }
    ensureMultiplexRunConnection(reason);
}

function beginMultiplexSyncBatch() {
    multiplexSyncSuspendDepth += 1;
}

function endMultiplexSyncBatch(reason = 'sync') {
    multiplexSyncSuspendDepth = Math.max(0, multiplexSyncSuspendDepth - 1);
    if (multiplexSyncSuspendDepth > 0) {
        return;
    }
    const pendingReason = multiplexSyncPendingReason || reason;
    multiplexSyncPendingReason = '';
    ensureMultiplexRunConnection(pendingReason);
}

function ensureMultiplexRunConnection(reason = 'sync') {
    const desiredConnections = desiredMultiplexRunConnections();
    if (desiredConnections.length === 0) {
        closeMultiplexRunConnection();
        return;
    }
    const signature = multiplexConnectionSignatureFor(desiredConnections);
    if (multiplexEventSource && multiplexConnectionSignature === signature) {
        syncMultiplexEventSourceHandle();
        return;
    }
    closeMultiplexRunConnection({ clearSignature: false });
    const url = multiplexRunEventsUrl(desiredConnections);
    multiplexConnectionSignature = signature;
    logInfo('frontend.sse.multiplex_opened', 'Multiplex run event stream opened', {
        reason,
        url,
        run_count: desiredConnections.length,
    });
    sysLog(`SSE multiplex ${reason} runs=${desiredConnections.length}`);
    const es = new EventSource(url);
    multiplexEventSource = es;
    multiplexReconnectDelayMs = 900;
    syncMultiplexEventSourceHandle();

    es.onmessage = event => {
        try {
            const data = JSON.parse(event.data);
            if (data.error) {
                logError('frontend.sse.multiplex_payload_error', 'Multiplex run stream returned error', {
                    error: data.error,
                });
                sysLog(`Run stream error: ${data.error}`, 'log-error');
                scheduleMultiplexReconnect();
                return;
            }
            applyMultiplexRunEvent(data);
        } catch (e) {
            logError(
                'frontend.sse.multiplex_parse_error',
                'Multiplex SSE parse error',
                errorToPayload(e, { raw: event.data }),
            );
        }
    };

    es.onerror = () => {
        if (multiplexEventSource !== es) {
            return;
        }
        logWarn('frontend.sse.multiplex_closed', 'Multiplex run event stream closed', {
            run_count: desiredMultiplexRunConnections().length,
        });
        sysLog('SSE multiplex closed.', 'log-error');
        multiplexEventSource.close();
        multiplexEventSource = null;
        multiplexConnectionSignature = '';
        syncMultiplexEventSourceHandle();
        scheduleMultiplexReconnect();
    };
}

function applyMultiplexRunEvent(data) {
    const runId = String(data.run_id || data.trace_id || '').trim();
    if (!runId) {
        return;
    }
    clearRunUnavailableCooldown(runId);
    const connection = resolveRunConnection(runId);
    if (!connection || connection.closed) {
        return;
    }
    const eventId = Number(data.event_id || 0);
    if (eventId > 0) {
        connection.lastEventId = Math.max(connection.lastEventId || 0, eventId);
        runStreamLastEventIds.set(runId, connection.lastEventId);
    }
    const evType = data.event_type;
    const payload = JSON.parse(data.payload_json || '{}');
    if (connection.mode === 'active') {
        routeEvent(evType, payload, data);
    } else {
        applyBackgroundRunEvent(connection, evType, payload, data);
    }

    if (isTerminalRunEvent(evType)) {
        logInfo('frontend.sse.terminal_event', 'Run stream reached terminal event', {
            run_id: runId,
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
}

function scheduleMultiplexReconnect() {
    if (multiplexReconnectTimer || desiredMultiplexRunConnections().length === 0) {
        return;
    }
    multiplexReconnectTimer = setTimeout(() => {
        multiplexReconnectTimer = null;
        const hasDesiredConnections = desiredMultiplexRunConnections().length > 0;
        if (!hasDesiredConnections) {
            closeMultiplexRunConnection();
            return;
        }
        multiplexConnectionSignature = '';
        ensureMultiplexRunConnection('multiplex-reconnect');
        multiplexReconnectDelayMs = Math.min(
            MULTIPLEX_RECONNECT_MAX_DELAY_MS,
            Math.max(900, multiplexReconnectDelayMs * 2),
        );
    }, multiplexReconnectDelayMs);
    multiplexReconnectTimer.unref?.();
}

function closeMultiplexRunConnection(options = {}) {
    if (multiplexReconnectTimer) {
        clearTimeout(multiplexReconnectTimer);
        multiplexReconnectTimer = null;
    }
    if (multiplexEventSource) {
        multiplexEventSource.close();
        multiplexEventSource = null;
    }
    if (options.clearSignature !== false) {
        multiplexConnectionSignature = '';
    }
    syncMultiplexEventSourceHandle();
}

function syncMultiplexEventSourceHandle() {
    const es = multiplexEventSource;
    if (activeConnection && !activeConnection.closed && !activeConnection.terminal) {
        activeConnection.eventSource = es;
        state.activeEventSource = es;
    } else {
        state.activeEventSource = null;
    }
    backgroundStreams.forEach(connection => {
        if (!connection.closed && !connection.terminal) {
            connection.eventSource = es;
        }
    });
}

function desiredMultiplexRunConnections() {
    const connections = [];
    if (activeConnection && !activeConnection.closed && !activeConnection.terminal) {
        connections.push(activeConnection);
    }
    Array.from(backgroundStreams.values())
        .filter(connection => connection && !connection.closed && !connection.terminal)
        .sort(compareRunStreamAttention)
        .forEach(connection => {
            if (!connections.some(item => item.runId === connection.runId)) {
                connections.push(connection);
            }
        });
    return connections.slice(0, MAX_MULTIPLEX_RUN_STREAMS);
}

function multiplexConnectionSignatureFor(connections) {
    return connections
        .map(connection => connection.runId)
        .join('|');
}

function multiplexRunEventsUrl(connections) {
    const params = new URLSearchParams();
    connections.forEach(connection => {
        params.append('run_id', connection.runId);
        params.append(
            'after_event_id',
            String(Number(connection.lastEventId || runStreamLastEventIds.get(connection.runId) || 0)),
        );
    });
    return `/api/runs/events?${params.toString()}`;
}

function resolveRunConnection(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return null;
    }
    if (activeConnection?.runId === safeRunId) {
        return activeConnection;
    }
    return backgroundStreams.get(safeRunId) || null;
}

function enforceMultiplexRunBudget(preferredRunId = '') {
    const preferred = String(preferredRunId || '').trim();
    const hiddenConnections = Array.from(backgroundStreams.values())
        .filter(connection => connection && !connection.closed && !connection.terminal)
        .sort(compareRunStreamAttention);
    const maxHidden = Math.max(
        0,
        MAX_MULTIPLEX_RUN_STREAMS - (activeConnection && !activeConnection.closed ? 1 : 0),
    );
    hiddenConnections.slice(maxHidden).forEach(connection => {
        if (preferred && connection.runId === preferred) {
            const replacement = hiddenConnections
                .slice(0, maxHidden)
                .reverse()
                .find(item => item.runId !== preferred);
            if (replacement) {
                finishBackgroundConnection(replacement, { rediscover: false, refreshSidebar: false });
            }
            return;
        }
        finishBackgroundConnection(connection, { rediscover: false, refreshSidebar: false });
    });
}

function compareRunStreamAttention(left, right) {
    return runStreamAttentionValue(right) - runStreamAttentionValue(left);
}

function runStreamAttentionValue(connection) {
    const runId = String(connection?.runId || '').trim();
    if (!runId) {
        return 0;
    }
    return Number(runStreamAttention.get(runId) || 0);
}

function touchRunStreamAttention(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    runStreamAttentionSequence += 1;
    runStreamAttention.set(safeRunId, runStreamAttentionSequence);
}

function finishActiveConnection(connection, finishOptions = {}) {
    if (!connection || connection.closed) return;
    const expectedToolCallIds = collectCoordinatorOverlayToolCallIds(connection.runId);
    connection.closed = true;
    clearReconnectTimer(connection);
    if (connection.eventSource && connection.eventSource !== multiplexEventSource) {
        connection.eventSource.close();
    }
    connection.eventSource = null;
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
    requestMultiplexRunConnection('finish-active');
    ensureNormalModeSubagentDiscoveryLoop();
    if (typeof connection.onCompleted === 'function') {
        Promise.resolve()
            .then(() => connection.onCompleted(connection.sessionId))
            .catch(error => {
                logError(
                    'frontend.run.completed_callback_failed',
                    'Run completion callback failed',
                    errorToPayload(error, {
                        session_id: connection.sessionId,
                        run_id: connection.runId,
                    }),
                );
            })
            .finally(() => {
                if (connection.sessionId) {
                    void refreshRoundsAfterCompletion(connection.sessionId, connection.runId, {
                        hydrate: false,
                        expectedToolCallIds,
                    });
                }
                ensureBackgroundDiscoveryLoop();
            });
        return;
    }
    if (connection.sessionId) {
        void refreshRoundsAfterCompletion(connection.sessionId, connection.runId, {
            expectedToolCallIds,
        });
    }
    ensureBackgroundDiscoveryLoop();
}

function collectCoordinatorOverlayToolCallIds(runId) {
    const overlay = messageRenderer.getCoordinatorStreamOverlay?.(runId);
    if (!overlay || typeof overlay !== 'object') {
        return [];
    }
    const parts = Array.isArray(overlay.parts) ? overlay.parts : [];
    const ids = [];
    parts.forEach(part => {
        if (String(part?.kind || '').trim() !== 'tool') {
            return;
        }
        const toolCallId = String(part.tool_call_id || part.toolCallId || part.id || '').trim();
        if (toolCallId) {
            ids.push(toolCallId);
        }
    });
    return ids;
}

function finishBackgroundConnection(connection, { rediscover = true, refreshSidebar = true } = {}) {
    if (!connection || connection.closed) return;
    connection.closed = true;
    clearReconnectTimer(connection);
    if (connection.eventSource && connection.eventSource !== multiplexEventSource) {
        connection.eventSource.close();
    }
    connection.eventSource = null;
    backgroundStreams.delete(connection.runId);
    requestMultiplexRunConnection('finish-background');
    if (refreshSidebar) {
        scheduleSessionsRefresh();
    }
    if (rediscover) {
        ensureBackgroundDiscoveryLoop();
    }
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
    const isCurrentSessionConnection = String(connection?.sessionId || '').trim()
        === String(state.currentSessionId || '').trim();
    if (isCurrentSessionConnection) {
        routeEvent(evType, payload, eventMeta);
        if (isTerminalRunEvent(evType)) {
            void refreshRoundsAfterCompletion(connection.sessionId, connection.runId, {
                expectedToolCallIds: collectCoordinatorOverlayToolCallIds(connection.runId),
            });
        }
        return;
    }

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
            eventId: eventMeta?.event_id || '',
            cleanupDelayMs: 4000,
        });
    }
    if (isTerminalRunEvent(evType)) {
        scheduleSessionsRefresh();
    }
}

function isTerminalRunEvent(evType) {
    return (
        evType === 'run_completed'
        || evType === 'run_failed'
        || evType === 'run_stopped'
        || evType === 'run_paused'
    );
}

function ensureBackgroundDiscoveryLoop() {
    if (!shouldRunBackgroundDiscovery()) {
        if (backgroundDiscoveryTimer) {
            clearTimeout(backgroundDiscoveryTimer);
            backgroundDiscoveryTimer = null;
        }
        return;
    }
    if (isBackgroundDiscoveryPaused()) {
        rescheduleBackgroundDiscoveryAfterPause();
        return;
    }
    if (backgroundDiscoveryTimer || backgroundDiscoveryPromise) {
        return;
    }
    backgroundDiscoveryTimer = setTimeout(() => {
        backgroundDiscoveryTimer = null;
        void runBackgroundDiscovery();
    }, 2500);
    backgroundDiscoveryTimer.unref?.();
}

function isBackgroundDiscoveryPaused(now = Date.now()) {
    return backgroundDiscoveryPausedUntil > now;
}

function rescheduleBackgroundDiscoveryAfterPause() {
    const remainingMs = Math.max(0, backgroundDiscoveryPausedUntil - Date.now());
    if (remainingMs <= 0) {
        return;
    }
    if (backgroundDiscoveryTimer) {
        clearTimeout(backgroundDiscoveryTimer);
        backgroundDiscoveryTimer = null;
    }
    backgroundDiscoveryTimer = setTimeout(() => {
        backgroundDiscoveryTimer = null;
        ensureBackgroundDiscoveryLoop();
    }, remainingMs + 40);
    backgroundDiscoveryTimer.unref?.();
}

function ensureNormalModeSubagentDiscoveryLoop() {
    if (!shouldRunNormalModeSubagentDiscovery()) {
        clearNormalModeSubagentDiscoveryTimer();
        return;
    }
    if (normalModeSubagentDiscoveryTimer || normalModeSubagentDiscoveryPromise) {
        return;
    }
    scheduleCurrentSessionSubagentDiscovery({
        delayMs: NORMAL_MODE_SUBAGENT_DISCOVERY_DELAY_MS,
    });
}

function clearNormalModeSubagentDiscoveryTimer() {
    if (!normalModeSubagentDiscoveryTimer) {
        return;
    }
    clearTimeout(normalModeSubagentDiscoveryTimer);
    normalModeSubagentDiscoveryTimer = null;
}

function shouldRunNormalModeSubagentDiscovery() {
    return !!(
        String(state.currentSessionId || '').trim()
        && String(state.currentSessionMode || '').trim().toLowerCase() === 'normal'
        && (
            isCurrentSessionParentRunActive()
            || isCurrentSessionRunStarting()
            || normalModeSubagentConnection
            || normalModeSubagentStreams.size > 0
            || state.activeSubagentSession
        )
    );
}

function isCurrentSessionParentRunActive() {
    const currentSessionId = String(state.currentSessionId || '').trim();
    if (!currentSessionId || !activeConnection || activeConnection.closed || activeConnection.terminal) {
        return false;
    }
    const connectionSessionId = String(
        activeConnection.sessionId || activeStreamSessionId || '',
    ).trim();
    return activeConnection.mode === 'active' && connectionSessionId === currentSessionId;
}

function isCurrentSessionRunStarting() {
    const currentSessionId = String(state.currentSessionId || '').trim();
    const pendingSessionId = String(pendingRunStart?.sessionId || '').trim();
    return !!(currentSessionId && pendingSessionId && pendingSessionId === currentSessionId);
}

async function runNormalModeSubagentDiscovery() {
    const sessionId = String(state.currentSessionId || '').trim();
    if (!sessionId) {
        clearNormalModeSubagentDiscoveryTimer();
        return;
    }
    if (normalModeSubagentDiscoveryPromise) {
        normalModeSubagentDiscoveryQueued = true;
        return;
    }
    normalModeSubagentDiscoveryPromise = (async () => {
        try {
            const payload = await fetchSessionSubagents(sessionId);
            if (String(state.currentSessionId || '').trim() !== sessionId) {
                return;
            }
            const subagentSessions = await import('../components/subagentSessions.js');
            if (typeof subagentSessions.replaceSessionSubagents === 'function') {
                subagentSessions.replaceSessionSubagents(sessionId, payload, {
                    emitChange: true,
                });
            } else {
                syncNormalModeSubagentStreams(sessionId, payload);
            }
        } catch (e) {
            logWarn('frontend.subagent.discovery_failed', 'Failed to discover normal-mode subagent streams', {
                session_id: sessionId,
                error: e?.message || String(e),
            });
        } finally {
            normalModeSubagentDiscoveryPromise = null;
            if (normalModeSubagentDiscoveryQueued) {
                normalModeSubagentDiscoveryQueued = false;
                scheduleCurrentSessionSubagentDiscovery({ delayMs: 0 });
                return;
            }
            ensureNormalModeSubagentDiscoveryLoop();
        }
    })();
    await normalModeSubagentDiscoveryPromise;
}

function shouldRunBackgroundDiscovery() {
    return !!(
        activeConnection
        || backgroundStreams.size > 0
        || pendingBackgroundAttachRunIds.size > 0
        || state.isGenerating
        || pendingRunStart
    );
}

async function runBackgroundDiscovery() {
    if (backgroundDiscoveryPromise) {
        backgroundDiscoveryQueued = true;
        return;
    }
    backgroundDiscoveryPromise = (async () => {
        try {
            if (isBackgroundDiscoveryPaused()) {
                return;
            }
            const sessions = await fetchSessions();
            if (isBackgroundDiscoveryPaused()) {
                return;
            }
            await reconcileBackgroundStreams(sessions);
        } catch (e) {
            logWarn('frontend.background.discovery_failed', 'Failed to discover running sessions', {
                error: e?.message || String(e),
            });
        } finally {
            backgroundDiscoveryPromise = null;
            if (backgroundDiscoveryQueued) {
                backgroundDiscoveryQueued = false;
                if (isBackgroundDiscoveryPaused()) {
                    ensureBackgroundDiscoveryLoop();
                    return;
                }
                void runBackgroundDiscovery();
                return;
            }
            ensureBackgroundDiscoveryLoop();
        }
    })();
    await backgroundDiscoveryPromise;
}

async function reconcileBackgroundStreams(sessionRecords = []) {
    if (isBackgroundDiscoveryPaused()) {
        return;
    }
    beginMultiplexSyncBatch();
    try {
        const records = Array.isArray(sessionRecords) ? sessionRecords : [];
        const focusedRunId = String(activeConnection?.runId || '').trim();
        const backgroundRunIds = new Set();
        backgroundStreams.forEach(connection => {
            if (connection?.runId) {
                backgroundRunIds.add(connection.runId);
            }
        });
        pendingBackgroundAttachRunIds.forEach(runId => {
            if (runId) {
                backgroundRunIds.add(runId);
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

        candidates.sort((left, right) => {
            const leftRunId = String(left?.active_run_id || '').trim();
            const rightRunId = String(right?.active_run_id || '').trim();
            const attentionDelta = Number(runStreamAttention.get(rightRunId) || 0)
                - Number(runStreamAttention.get(leftRunId) || 0);
            if (attentionDelta !== 0) {
                return attentionDelta;
            }
            return backgroundRecordTimestamp(right) - backgroundRecordTimestamp(left);
        });

        const desiredRunIds = new Set();
        for (const record of candidates) {
            const runId = String(record?.active_run_id || '').trim();
            if (!runId) {
                continue;
            }
            if (focusedRunId && runId === focusedRunId) {
                desiredRunIds.add(runId);
                continue;
            }
            if (desiredRunIds.size >= MAX_MULTIPLEX_RUN_STREAMS) {
                break;
            }
            desiredRunIds.add(runId);
            if (backgroundRunIds.has(runId)) {
                continue;
            }
            attachBackgroundStreamForSession(record);
            backgroundRunIds.add(runId);
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
    } finally {
        endMultiplexSyncBatch('background-reconcile');
    }
}

function backgroundRecordTimestamp(record) {
    const raw = String(record?.updated_at || record?.updatedAt || '').trim();
    if (!raw) {
        return 0;
    }
    const parsed = Date.parse(raw);
    return Number.isNaN(parsed) ? 0 : parsed;
}

function attachBackgroundStreamForSession(record) {
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
    if (backgroundStreams.has(runId) || pendingBackgroundAttachRunIds.has(runId)) {
        return true;
    }
    if (activeConnection?.runId === runId) {
        return true;
    }

    pendingBackgroundAttachRunIds.add(runId);
    try {
        clearSessionUnavailableCooldown(sessionId);
        const sessionMode = String(record?.session_mode || 'normal').trim().toLowerCase();
        const recordPrimaryRoleId = String(
            record?.active_run_primary_role_id
            || record?.activeRunPrimaryRoleId
            || record?.primary_role_id
            || '',
        ).trim();
        setRunPrimaryRole(runId, recordPrimaryRoleId || null);
        const knownEventId = Number(runStreamLastEventIds.get(runId) || 0);
        const afterEventId = knownEventId > 0 ? knownEventId : 0;
        const connection = {
            runId,
            sessionId,
            eventSource: null,
            onCompleted: null,
            mode: 'background',
            lastEventId: afterEventId,
            primaryRoleId: String(
                recordPrimaryRoleId || getRunPrimaryRoleId(runId, sessionMode) || getPrimaryRoleId(sessionMode) || ''
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
        logWarn('frontend.background.attach_failed', 'Failed to attach background stream', {
            session_id: sessionId,
            run_id: runId,
            error: e?.message || String(e),
        });
        return false;
    } finally {
        pendingBackgroundAttachRunIds.delete(runId);
    }
}

function shouldAttachNormalModeSubagentStream(record) {
    const runStatus = String(record?.runStatus || record?.run_status || '').trim().toLowerCase();
    const instanceStatus = String(record?.status || '').trim().toLowerCase();
    const effectiveStatus = runStatus || instanceStatus;
    return effectiveStatus === 'queued' || effectiveStatus === 'running' || effectiveStatus === 'stopping';
}

function resolveSubagentAfterEventId(record) {
    const lastEventId = Number(record?.lastEventId || record?.last_event_id || 0);
    if (lastEventId > 0) {
        return lastEventId;
    }
    const checkpointEventId = Number(
        record?.checkpointEventId || record?.checkpoint_event_id || 0,
    );
    if (checkpointEventId > 0) {
        return checkpointEventId;
    }
    return 0;
}

function desiredNormalModeSubagentRunIds(records) {
    const desiredRunIds = new Set();
    const rows = Array.isArray(records) ? records : [];
    for (const record of rows) {
        const safeRunId = String(record?.runId || record?.run_id || '').trim();
        if (!safeRunId || isRunUnavailable(safeRunId)) {
            continue;
        }
        if (shouldAttachNormalModeSubagentStream(record)) {
            desiredRunIds.add(safeRunId);
        }
    }
    return desiredRunIds;
}

function resolveSubagentSessionAfterEventId(records) {
    const rows = Array.isArray(records) ? records : [];
    let afterEventId = 0;
    for (const record of rows) {
        afterEventId = Math.max(afterEventId, resolveSubagentAfterEventId(record));
    }
    return afterEventId;
}

function openNormalModeSubagentSessionStream(sessionId, records, desiredRunIds) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId || isSessionUnavailable(safeSessionId)) {
        return;
    }
    const afterEventId = resolveSubagentSessionAfterEventId(records);
    const connection = {
        sessionId: safeSessionId,
        eventSource: null,
        closed: false,
        terminal: false,
        reconnectTimer: null,
        lastEventId: afterEventId,
        desiredRunIds,
    };
    normalModeSubagentConnection = connection;
    openNormalModeSubagentSessionStreamConnection(connection, {
        afterEventId: afterEventId > 0 ? afterEventId : null,
        reason: 'subagent-session-discovery',
    });
}

function openNormalModeSubagentSessionStreamConnection(connection, { afterEventId = null, reason } = {}) {
    if (!connection || connection.closed) {
        return;
    }
    const urlParams = afterEventId !== null ? `?after_event_id=${afterEventId}` : '';
    const url = `/api/sessions/${connection.sessionId}/subagents/events${urlParams}`;
    logInfo('frontend.subagent_session_sse.opened', 'Normal-mode session subagent stream opened', {
        session_id: connection.sessionId,
        reason: reason || 'subagent-session',
        url,
    });
    const es = new EventSource(url);
    connection.eventSource = es;
    connection.closed = false;
    connection.terminal = false;
    clearReconnectTimer(connection);
    normalModeSubagentConnection = connection;

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.error) {
                if (isSessionNotFoundError(data.error)) {
                    markSessionUnavailable(connection.sessionId);
                }
                logError('frontend.subagent_session_sse.payload_error', 'Normal-mode session subagent stream returned error', {
                    session_id: connection.sessionId,
                    error: data.error,
                });
                finishNormalModeSubagentSessionConnection(connection);
                return;
            }
            const eventId = Number(data.event_id || 0);
            if (eventId > 0) {
                connection.lastEventId = Math.max(connection.lastEventId, eventId);
            }
            const runId = String(data.run_id || data.trace_id || '').trim();
            if (!runId.startsWith('subagent_run_')) {
                return;
            }
            clearRunUnavailableCooldown(runId);
            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');
            routeEvent(evType, payload, data);
            if (isTerminalRunEvent(evType)) {
                scheduleCurrentSessionSubagentDiscovery({ delayMs: 250 });
            }
        } catch (e) {
            logError(
                'frontend.subagent_session_sse.parse_error',
                'Normal-mode session subagent SSE parse error',
                errorToPayload(e, { session_id: connection.sessionId, raw: event.data }),
            );
        }
    };

    es.onerror = () => {
        if (connection.closed || connection.terminal) {
            return;
        }
        logWarn('frontend.subagent_session_sse.closed', 'Normal-mode session subagent stream closed', {
            session_id: connection.sessionId,
        });
        if (connection.eventSource) {
            connection.eventSource.close();
            connection.eventSource = null;
        }
        scheduleSubagentSessionReconnect(connection);
    };
}

function scheduleSubagentSessionReconnect(connection) {
    if (!connection || connection.closed || connection.terminal) {
        return;
    }
    clearReconnectTimer(connection);
    connection.reconnectTimer = setTimeout(() => {
        connection.reconnectTimer = null;
        if (connection.closed || connection.terminal) {
            return;
        }
        if (
            connection.sessionId !== String(state.currentSessionId || '').trim()
            || String(state.currentSessionMode || '').trim().toLowerCase() !== 'normal'
        ) {
            finishNormalModeSubagentSessionConnection(connection);
            return;
        }
        openNormalModeSubagentSessionStreamConnection(connection, {
            afterEventId: connection.lastEventId > 0 ? connection.lastEventId : null,
            reason: 'subagent-session-reconnect',
        });
    }, NORMAL_MODE_SUBAGENT_RECONNECT_DELAY_MS);
}

function finishNormalModeSubagentSessionConnection(connection) {
    if (!connection || connection.closed) {
        return;
    }
    connection.closed = true;
    clearReconnectTimer(connection);
    if (connection.eventSource) {
        connection.eventSource.close();
        connection.eventSource = null;
    }
    if (normalModeSubagentConnection === connection) {
        normalModeSubagentConnection = null;
    }
}

function openNormalModeSubagentRunStream(record, sessionId) {
    const runId = String(record?.runId || record?.run_id || '').trim();
    const safeSessionId = String(sessionId || '').trim();
    if (!runId || !safeSessionId || normalModeSubagentStreams.has(runId)) {
        return;
    }
    const connection = {
        runId,
        sessionId: safeSessionId,
        instanceId: String(record?.instanceId || record?.instance_id || '').trim(),
        roleId: String(record?.roleId || record?.role_id || '').trim(),
        eventSource: null,
        closed: false,
        terminal: false,
        reconnectTimer: null,
        lastEventId: resolveSubagentAfterEventId(record),
    };
    openNormalModeSubagentRunStreamConnection(connection, {
        afterEventId: connection.lastEventId > 0 ? connection.lastEventId : null,
        reason: 'subagent-discovery',
    });
}

function openNormalModeSubagentRunStreamConnection(connection, { afterEventId = null, reason } = {}) {
    const urlParams = afterEventId !== null ? `?after_event_id=${afterEventId}` : '';
    const url = `/api/runs/${connection.runId}/events${urlParams}`;
    logInfo('frontend.subagent_sse.opened', 'Normal-mode subagent run stream opened', {
        run_id: connection.runId,
        session_id: connection.sessionId,
        reason: reason || 'subagent',
        url,
    });
    const es = new EventSource(url);
    connection.eventSource = es;
    connection.closed = false;
    connection.terminal = false;
    clearReconnectTimer(connection);
    normalModeSubagentStreams.set(connection.runId, connection);

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.error) {
                if (isRunNotFoundError(data.error)) {
                    markRunUnavailable(connection.runId);
                }
                logError('frontend.subagent_sse.payload_error', 'Normal-mode subagent stream returned error', {
                    run_id: connection.runId,
                    error: data.error,
                });
                finishNormalModeSubagentConnection(connection);
                return;
            }
            const eventId = Number(data.event_id || 0);
            if (eventId > 0) {
                connection.lastEventId = Math.max(connection.lastEventId, eventId);
            }
            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');
            routeEvent(evType, payload, data);
            if (isTerminalRunEvent(evType)) {
                connection.terminal = true;
                finishNormalModeSubagentConnection(connection);
                scheduleSessionsRefresh();
            }
        } catch (e) {
            logError(
                'frontend.subagent_sse.parse_error',
                'Normal-mode subagent SSE parse error',
                errorToPayload(e, { run_id: connection.runId, raw: event.data }),
            );
        }
    };

    es.onerror = () => {
        if (connection.closed || connection.terminal) {
            return;
        }
        logWarn('frontend.subagent_sse.closed', 'Normal-mode subagent stream closed', {
            run_id: connection.runId,
            session_id: connection.sessionId,
        });
        if (connection.eventSource) {
            connection.eventSource.close();
            connection.eventSource = null;
        }
        scheduleSubagentReconnect(connection);
    };
}

function scheduleSubagentReconnect(connection) {
    if (!connection || connection.closed || connection.terminal) {
        return;
    }
    clearReconnectTimer(connection);
    connection.reconnectTimer = setTimeout(() => {
        connection.reconnectTimer = null;
        if (connection.closed || connection.terminal) {
            return;
        }
        if (
            connection.sessionId !== String(state.currentSessionId || '').trim()
            || String(state.currentSessionMode || '').trim().toLowerCase() !== 'normal'
        ) {
            finishNormalModeSubagentConnection(connection);
            return;
        }
        openNormalModeSubagentRunStreamConnection(connection, {
            afterEventId: connection.lastEventId > 0 ? connection.lastEventId : null,
            reason: 'subagent-reconnect',
        });
    }, 1000);
}

function finishNormalModeSubagentConnection(connection) {
    if (!connection || connection.closed) {
        return;
    }
    connection.closed = true;
    clearReconnectTimer(connection);
    if (connection.eventSource) {
        connection.eventSource.close();
        connection.eventSource = null;
    }
    normalModeSubagentStreams.delete(connection.runId);
    ensureNormalModeSubagentDiscoveryLoop();
}

function isRunNotFoundError(errorMessage) {
    const safe = String(errorMessage || '').toLowerCase();
    return safe.includes('not found') && safe.includes('run');
}

function isSessionNotFoundError(errorMessage) {
    const safe = String(errorMessage || '').toLowerCase();
    return safe.includes('not found') && safe.includes('session');
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
    applyLocalStoppedSubagentSnapshot(safeSessionId);
    await applyLocalStoppedSnapshot(safeRunId, safeSessionId);
    await syncRecoveryAfterStopRequest(safeRunId, safeSessionId);
}

function applyLocalStoppedSubagentSnapshot(sessionId) {
    const safeSessionId = String(sessionId || state.currentSessionId || '').trim();
    if (!safeSessionId) return;
    markNormalModeSubagentSessionsStoppedForParent(safeSessionId);
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
                    pending_user_question_count: 0,
                    stream_connected: false,
                    should_show_recover: true,
                },
                pending_tool_approvals: [],
                pending_user_questions: [],
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
