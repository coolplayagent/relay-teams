/**
 * app/session.js
 * Session selection state and UI synchronization.
 */
import { clearAllPanels } from '../components/agentPanel.js';
import { clearContextIndicators, scheduleCoordinatorContextPreview } from '../components/contextIndicators.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import { clearSessionTokenUsage, scheduleSessionTokenUsageRefresh } from '../components/sessionTokenUsage.js';
import { syncSessionDebugBadge } from '../components/sessionDebugBadge.js';
import { markSidebarSessionTerminalViewed } from '../components/sessionSidebarStore.js';
import { hideProjectView } from '../components/projectView.js';
import { clearNewSessionDraft } from '../components/newSessionDraft.js';
import { markSubagentRailLoading } from '../components/subagentRail.js';
import { setRoundsMode } from '../components/sidebar.js';
import {
    clearActiveSubagentSession,
    ensureSessionSubagents,
    getSessionSubagentSessions,
    openSubagentSession,
} from '../components/subagentSessions.js';
import { fetchSessionHistory, markSessionTerminalRunViewed } from '../core/api.js';
import {
    clearSessionRecovery,
    hydrateSessionView,
    stopSessionContinuity,
} from './recovery.js';
import { applyCurrentSessionRecord, resetCurrentSessionTopology, state } from '../core/state.js';
import {
    detachActiveStreamForSessionSwitch,
    detachNormalModeSubagentStreamsForSessionSwitch,
    prepareStreamsForForegroundNavigation,
} from '../core/stream.js';
import { detachForegroundSubmission } from '../core/submission.js';
import { els } from '../utils/dom.js';
import { formatMessage, t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';
import { refreshSessionTopologyControls } from './prompt.js';

let sessionSelectionToken = 0;
let sessionSelectionController = null;
let sessionSelectionTargetId = '';
let sessionSwitchLoadingTimer = null;
let sessionSwitchReadyTimer = null;
let sessionSelectionCancellationBound = false;
const SESSION_SWITCH_LOADING_DELAY_MS = 80;
const SESSION_SWITCH_READY_MS = 140;
const TERMINAL_VIEW_RETRY_DELAY_MS = 250;
const TERMINAL_VIEW_MAX_ATTEMPTS = 3;

bindSessionSelectionCancellation();

function isLatestSessionSelection(token, sessionId) {
    return token === sessionSelectionToken && state.currentSessionId === sessionId;
}

export async function selectSession(sessionId) {
    bindSessionSelectionCancellation();
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    const selectionToken = ++sessionSelectionToken;
    const selectionController = resetSessionSelectionController();
    sessionSelectionTargetId = safeSessionId;
    const selectionSignal = selectionController.signal;
    const hadActiveSubagentSession = !!state.activeSubagentSession;
    const isSameSession = state.currentSessionId === safeSessionId && !hadActiveSubagentSession;
    const previousSessionId = state.currentSessionId;
    const selectedSessionEl = document.querySelector(
        `.session-item[data-session-id="${safeSessionId}"]`,
    );
    const selectedSessionNeedsTerminalView = (
        selectedSessionEl?.classList?.contains?.('has-run-indicator-unread') === true
        || selectedSessionEl?.classList?.contains?.('has-run-indicator-failed') === true
        || selectedSessionEl?.classList?.contains?.('has-run-indicator-stopped') === true
    );
    const selectedWorkspaceId = String(
        selectedSessionEl?.getAttribute('data-workspace-id') || '',
    ).trim();
    if (selectedWorkspaceId) {
        state.currentWorkspaceId = selectedWorkspaceId;
    }
    if (isSameSession && (state.isGenerating || state.activeEventSource)) {
        try {
            await hydrateSessionView(safeSessionId, {
                includeRounds: false,
                quiet: true,
                signal: selectionSignal,
            });
        } catch (error) {
            if (error?.name === 'AbortError') {
                return;
            }
            throw error;
        } finally {
            clearSessionSelectionController(selectionController);
        }
        if (!isLatestSessionSelection(selectionToken, safeSessionId)) {
            return;
        }
        if (selectedSessionNeedsTerminalView) {
            void markSelectedSessionTerminalViewed(safeSessionId, selectionSignal);
        }
        scheduleSessionTokenUsageRefresh({ immediate: true });
        sysLog(`Synced live session: ${safeSessionId}`);
        return;
    }
    if (!isSameSession) {
        detachForegroundSubmission({ focusPrompt: false });
        detachActiveStreamForSessionSwitch({ focusPrompt: false });
    }
    if (!isSameSession && previousSessionId) {
        stopSessionContinuity(previousSessionId);
        detachNormalModeSubagentStreamsForSessionSwitch(previousSessionId);
    }
    state.currentSessionId = safeSessionId;
    if (!isSameSession) {
        prepareStreamsForForegroundNavigation(safeSessionId);
    }
    syncSessionDebugBadge(safeSessionId);
    clearNewSessionDraft();
    state.instanceRoleMap = {};
    state.roleInstanceMap = {};
    state.taskInstanceMap = {};
    state.taskStatusMap = {};
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.autoSwitchedSubagentInstances = {};
    state.pausedSubagent = null;
    state.sessionAgents = [];
    state.sessionTasks = [];
    state.selectedRoleId = null;
    markSubagentRailLoading(safeSessionId);
    clearActiveSubagentSession();
    resetCurrentSessionTopology();
    clearSessionRecovery();

    document.querySelectorAll('.session-item').forEach(el => {
        const isActive = el.getAttribute('data-session-id') === safeSessionId;
        el.classList.toggle('active', isActive);
    });
    document.dispatchEvent(
        new CustomEvent('agent-teams-session-activated', {
            detail: { sessionId: safeSessionId },
        }),
    );

    hideProjectView();
    setRoundsMode();
    state.agentViews = { main: els.chatMessages };
    state.activeView = 'main';
    clearAllPanels();
    clearContextIndicators({ preserveDisplay: true });
    clearSessionTokenUsage({ preserveDisplay: true });
    clearAllStreamState({ preserveOverlay: true });
    refreshSessionTopologyControls();
    beginSessionSwitchLoading(selectionToken, safeSessionId);

    try {
        const sessionRecordPromise = fetchSessionHistory(safeSessionId, {
            priority: 'high',
            signal: selectionSignal,
        });
        sessionRecordPromise.catch(() => null);
        const hydratePromise = hydrateSessionView(safeSessionId, {
            includeRounds: true,
            priority: 'high',
            quiet: true,
            signal: selectionSignal,
        });
        hydratePromise.catch(() => null);
        const sessionRecord = await sessionRecordPromise;
        if (!isLatestSessionSelection(selectionToken, safeSessionId)) {
            return;
        }
        const sessionNeedsTerminalView = selectedSessionNeedsTerminalView
            || sessionRecordNeedsTerminalView(sessionRecord);
        applyCurrentSessionRecord(sessionRecord);
        refreshSessionTopologyControls();
        await hydratePromise;
        if (!isLatestSessionSelection(selectionToken, safeSessionId)) {
            return;
        }
        if (sessionNeedsTerminalView) {
            void markSelectedSessionTerminalViewed(safeSessionId, selectionSignal);
        }
    } catch (error) {
        if (error?.name === 'AbortError') {
            return;
        }
        throw error;
    } finally {
        finishSessionSwitchLoading(selectionToken, safeSessionId);
        clearSessionSelectionController(selectionController);
    }
    scheduleCoordinatorContextPreview({ immediate: true });
    scheduleSessionTokenUsageRefresh({ immediate: true });
    void ensureSessionSubagents(safeSessionId, {
        force: true,
        signal: selectionSignal,
    });
    document.dispatchEvent(
        new CustomEvent('agent-teams-session-selected', {
            detail: { sessionId: safeSessionId },
        }),
    );
    sysLog(formatMessage(isSameSession ? 'session.reloaded' : 'session.switched', {
        session_id: safeSessionId,
    }));
}

function sessionRecordNeedsTerminalView(sessionRecord) {
    if (!sessionRecord || typeof sessionRecord !== 'object') {
        return false;
    }
    if (sessionRecord.has_unread_terminal_run === true) {
        return true;
    }
    const status = String(
        sessionRecord.latest_terminal_run_status || sessionRecord.latestTerminalRunStatus || '',
    ).trim().toLowerCase();
    return status === 'failed' || status === 'stopped';
}

function resetSessionSelectionController() {
    if (sessionSelectionController) {
        sessionSelectionController.abort();
    }
    sessionSelectionController = new AbortController();
    return sessionSelectionController;
}

function clearSessionSelectionController(controller) {
    if (sessionSelectionController === controller) {
        sessionSelectionController = null;
        sessionSelectionTargetId = '';
    }
}

function beginSessionSwitchLoading(selectionToken, sessionId) {
    const chatContainer = els.chatContainer || els.chatMessages?.parentElement || null;
    if (!chatContainer?.classList) {
        return;
    }
    clearTimeout(sessionSwitchLoadingTimer);
    clearTimeout(sessionSwitchReadyTimer);
    chatContainer.classList.remove('is-session-switch-ready');
    chatContainer.classList.add('is-session-switch-pending');
    ensureSessionSwitchLoadingNode(chatContainer);
    sessionSwitchLoadingTimer = globalThis.setTimeout(() => {
        if (!isLatestSessionSelection(selectionToken, sessionId)) {
            return;
        }
        chatContainer.classList.add('is-session-switching');
    }, SESSION_SWITCH_LOADING_DELAY_MS);
}

function finishSessionSwitchLoading(selectionToken, sessionId) {
    if (!isLatestSessionSelection(selectionToken, sessionId)) {
        return;
    }
    const chatContainer = els.chatContainer || els.chatMessages?.parentElement || null;
    if (!chatContainer?.classList) {
        return;
    }
    clearTimeout(sessionSwitchLoadingTimer);
    clearTimeout(sessionSwitchReadyTimer);
    chatContainer.classList.remove('is-session-switch-pending', 'is-session-switching');
    chatContainer.classList.add('is-session-switch-ready');
    sessionSwitchReadyTimer = globalThis.setTimeout(() => {
        if (!isLatestSessionSelection(selectionToken, sessionId)) {
            return;
        }
        chatContainer.classList.remove('is-session-switch-ready');
    }, SESSION_SWITCH_READY_MS);
}

function ensureSessionSwitchLoadingNode(chatContainer) {
    if (chatContainer.querySelector?.('.session-switch-loading')) {
        return;
    }
    if (typeof document === 'undefined' || typeof document.createElement !== 'function') {
        return;
    }
    const loadingNode = document.createElement('div');
    loadingNode.className = 'session-switch-loading';
    loadingNode.setAttribute('aria-live', 'polite');
    loadingNode.innerHTML = `
        <span class="session-switch-loading-spinner" aria-hidden="true"></span>
        <span>${t('session.loading')}</span>
    `;
    chatContainer.appendChild?.(loadingNode);
}

function bindSessionSelectionCancellation() {
    if (
        sessionSelectionCancellationBound
        || typeof document === 'undefined'
        || typeof document.addEventListener !== 'function'
    ) {
        return;
    }
    sessionSelectionCancellationBound = true;
    document.addEventListener('agent-teams-session-selection-cancelled', () => {
        cancelActiveSessionSelection();
    });
}

function cancelActiveSessionSelection() {
    sessionSelectionToken += 1;
    if (sessionSelectionController) {
        sessionSelectionController.abort();
        sessionSelectionController = null;
    }
    sessionSelectionTargetId = '';
    clearSessionSwitchLoading();
}

function clearSessionSwitchLoading() {
    clearTimeout(sessionSwitchLoadingTimer);
    clearTimeout(sessionSwitchReadyTimer);
    const chatContainer = els.chatContainer || els.chatMessages?.parentElement || null;
    chatContainer?.classList?.remove?.(
        'is-session-switch-pending',
        'is-session-switching',
        'is-session-switch-ready',
    );
}

async function markSelectedSessionTerminalViewed(sessionId, signal = null) {
    try {
        for (let attempt = 1; attempt <= TERMINAL_VIEW_MAX_ATTEMPTS; attempt += 1) {
            if (signal?.aborted) {
                return;
            }
            let response;
            try {
                response = await markSessionTerminalRunViewed(sessionId, { signal });
            } catch (error) {
                if (error?.name === 'AbortError') {
                    return;
                }
                if (
                    !isTerminalViewRetryableError(error)
                    || attempt >= TERMINAL_VIEW_MAX_ATTEMPTS
                ) {
                    throw error;
                }
                await waitForTerminalViewRetry(signal);
                continue;
            }
            if (response?.status !== 'deferred') {
                if (!signal?.aborted) {
                    markSidebarSessionTerminalViewed(sessionId);
                }
                return;
            }
            if (attempt < TERMINAL_VIEW_MAX_ATTEMPTS) {
                await waitForTerminalViewRetry(signal);
            }
        }
    } catch (error) {
        if (error?.name === 'AbortError') {
            return;
        }
        sysLog(
            formatMessage('session.terminal_view_mark_failed', {
                error: error?.message || String(error),
            }),
            'log-error',
        );
    }
}

function isTerminalViewRetryableError(error) {
    return Number(error?.status || 0) === 503;
}

function waitForTerminalViewRetry(signal = null) {
    if (signal?.aborted) {
        return Promise.resolve();
    }
    return new Promise(resolve => {
        const timeout = setTimeout(resolve, TERMINAL_VIEW_RETRY_DELAY_MS);
        signal?.addEventListener?.(
            'abort',
            () => {
                clearTimeout(timeout);
                resolve();
            },
            { once: true },
        );
    });
}

export async function selectSubagentSession(sessionId, subagent) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(
        subagent?.instanceId || subagent?.instance_id || '',
    ).trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    const isParentSelectionPending = (
        sessionSelectionController
        && sessionSelectionTargetId === safeSessionId
    );
    if (isParentSelectionPending) {
        cancelActiveSessionSelection();
        state.currentSessionId = safeSessionId;
        syncSessionDebugBadge(safeSessionId);
    }
    if (state.currentSessionId !== safeSessionId) {
        await selectSession(safeSessionId);
        if (state.currentSessionId !== safeSessionId) {
            return;
        }
    }
    cancelActiveSessionSelection();
    const fallback = {
        sessionId: safeSessionId,
        instanceId: safeInstanceId,
        roleId: String(subagent?.roleId || subagent?.role_id || '').trim(),
        runId: String(subagent?.runId || subagent?.run_id || '').trim(),
        title: String(subagent?.title || '').trim(),
        status: String(subagent?.status || 'idle').trim() || 'idle',
    };
    const cachedRecords = getSessionSubagentSessions(safeSessionId);
    const resolved = cachedRecords.find(item => item.instanceId === safeInstanceId) || fallback;
    hideProjectView();
    setRoundsMode();
    void openSubagentSession(safeSessionId, resolved);
    void ensureSessionSubagents(safeSessionId, { force: false }).catch(error => {
        if (error?.name === 'AbortError') {
            return;
        }
        sysLog(formatMessage('sidebar.error.selecting_session', {
            error: error?.message || String(error),
        }), 'log-error');
    });
    document.dispatchEvent(
        new CustomEvent('agent-teams-subagent-session-selected', {
            detail: {
                sessionId: safeSessionId,
                instanceId: safeInstanceId,
            },
        }),
    );
}
