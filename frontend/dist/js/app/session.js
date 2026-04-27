/**
 * app/session.js
 * Session selection state and UI synchronization.
 */
import { clearAllPanels } from '../components/agentPanel.js';
import { clearContextIndicators, scheduleCoordinatorContextPreview } from '../components/contextIndicators.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import { clearSessionTokenUsage, scheduleSessionTokenUsageRefresh } from '../components/sessionTokenUsage.js';
import { syncSessionDebugBadge } from '../components/sessionDebugBadge.js';
import { hideProjectView } from '../components/projectView.js';
import { clearNewSessionDraft } from '../components/newSessionDraft.js';
import { setRoundsMode } from '../components/sidebar.js';
import {
    clearActiveSubagentSession,
    ensureSessionSubagents,
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
} from '../core/stream.js';
import { detachForegroundSubmission } from '../core/submission.js';
import { els } from '../utils/dom.js';
import { formatMessage, t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';
import { refreshSessionTopologyControls } from './prompt.js';

let sessionSelectionToken = 0;
let sessionSelectionController = null;
let sessionSwitchLoadingTimer = null;
let sessionSwitchReadyTimer = null;
const SESSION_SWITCH_LOADING_DELAY_MS = 80;
const SESSION_SWITCH_READY_MS = 140;
const TERMINAL_VIEW_RETRY_DELAY_MS = 250;
const TERMINAL_VIEW_MAX_ATTEMPTS = 3;

function isLatestSessionSelection(token, sessionId) {
    return token === sessionSelectionToken && state.currentSessionId === sessionId;
}

export async function selectSession(sessionId) {
    const selectionToken = ++sessionSelectionToken;
    const selectionController = resetSessionSelectionController();
    const selectionSignal = selectionController.signal;
    const isSameSession = state.currentSessionId === sessionId && !state.activeSubagentSession;
    const previousSessionId = state.currentSessionId;
    const selectedSessionEl = document.querySelector(
        `.session-item[data-session-id="${sessionId}"]`,
    );
    const selectedWorkspaceId = String(
        selectedSessionEl?.getAttribute('data-workspace-id') || '',
    ).trim();
    if (selectedWorkspaceId) {
        state.currentWorkspaceId = selectedWorkspaceId;
    }
    if (isSameSession && (state.isGenerating || state.activeEventSource)) {
        try {
            await hydrateSessionView(sessionId, {
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
        if (!isLatestSessionSelection(selectionToken, sessionId)) {
            return;
        }
        scheduleSessionTokenUsageRefresh({ immediate: true });
        sysLog(`Synced live session: ${sessionId}`);
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
    state.currentSessionId = sessionId;
    syncSessionDebugBadge(sessionId);
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
    clearActiveSubagentSession();
    resetCurrentSessionTopology();
    clearSessionRecovery();

    document.querySelectorAll('.session-item').forEach(el => {
        const isActive = el.getAttribute('data-session-id') === sessionId;
        el.classList.toggle('active', isActive);
    });
    document.dispatchEvent(
        new CustomEvent('agent-teams-session-activated', {
            detail: { sessionId },
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
    beginSessionSwitchLoading(selectionToken, sessionId);

    try {
        const sessionRecord = await fetchSessionHistory(sessionId, {
            signal: selectionSignal,
        });
        if (!isLatestSessionSelection(selectionToken, sessionId)) {
            return;
        }
        applyCurrentSessionRecord(sessionRecord);
        refreshSessionTopologyControls();
        await hydrateSessionView(sessionId, {
            includeRounds: true,
            quiet: true,
            signal: selectionSignal,
        });
        if (!isLatestSessionSelection(selectionToken, sessionId)) {
            return;
        }
    } catch (error) {
        if (error?.name === 'AbortError') {
            return;
        }
        throw error;
    } finally {
        finishSessionSwitchLoading(selectionToken, sessionId);
        clearSessionSelectionController(selectionController);
    }
    void markSelectedSessionTerminalViewed(sessionId, selectionSignal);
    scheduleCoordinatorContextPreview({ immediate: true });
    scheduleSessionTokenUsageRefresh({ immediate: true });
    void ensureSessionSubagents(sessionId, {
        force: true,
        signal: selectionSignal,
    });
    document.dispatchEvent(
        new CustomEvent('agent-teams-session-selected', {
            detail: { sessionId },
        }),
    );
    sysLog(formatMessage(isSameSession ? 'session.reloaded' : 'session.switched', {
        session_id: sessionId,
    }));
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
    if (state.currentSessionId !== safeSessionId) {
        await selectSession(safeSessionId);
        if (state.currentSessionId !== safeSessionId) {
            return;
        }
    }
    const records = await ensureSessionSubagents(safeSessionId, { force: false });
    const resolved = records.find(item => item.instanceId === safeInstanceId)
        || {
            sessionId: safeSessionId,
            instanceId: safeInstanceId,
            roleId: String(subagent?.roleId || subagent?.role_id || '').trim(),
            runId: String(subagent?.runId || subagent?.run_id || '').trim(),
            title: String(subagent?.title || '').trim(),
            status: String(subagent?.status || 'idle').trim() || 'idle',
        };
    hideProjectView();
    setRoundsMode();
    await openSubagentSession(safeSessionId, resolved);
    document.dispatchEvent(
        new CustomEvent('agent-teams-subagent-session-selected', {
            detail: {
                sessionId: safeSessionId,
                instanceId: safeInstanceId,
            },
        }),
    );
}
