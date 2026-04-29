/**
 * app/sessionView.js
 * View-level session hydration boundaries shared by main and subagent navigation.
 */
import {
    hydrateSessionSwitchView,
    hydrateSessionView,
} from './recovery.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';

let mainSessionRestoreController = null;
let mainSessionRestoreToken = 0;

export async function hydrateMainSessionForSwitch(
    sessionId,
    {
        priority = 'high',
        quiet = true,
        roundsScrollPolicy = '',
        signal = null,
    } = {},
) {
    return hydrateSessionSwitchView(sessionId, {
        priority,
        quiet,
        roundsScrollPolicy,
        signal,
    });
}

export async function restoreMainSessionView(sessionId, { quiet = true } = {}) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        abortMainSessionRestore();
        return null;
    }

    const restoreRequest = resetMainSessionRestoreController();
    const restoreController = restoreRequest.controller;
    const restoreToken = restoreRequest.token;
    const restoreSignal = restoreController.signal;
    showMainSessionLoadingPlaceholder(safeSessionId);
    document.dispatchEvent(new CustomEvent('agent-teams-subagent-session-cleared', {
        detail: { sessionId: safeSessionId },
    }));
    try {
        const snapshot = await hydrateSessionView(safeSessionId, {
            includeRounds: true,
            quiet,
            signal: restoreSignal,
        });
        if (
            restoreSignal.aborted
            || !isLatestMainSessionRestore(restoreToken, restoreController, safeSessionId)
            || String(state.currentSessionId || '').trim() !== safeSessionId
            || state.activeSubagentSession
        ) {
            return null;
        }
        document.dispatchEvent(new CustomEvent('agent-teams-session-activated', {
            detail: { sessionId: safeSessionId },
        }));
        document.dispatchEvent(new CustomEvent('agent-teams-session-selected', {
            detail: { sessionId: safeSessionId },
        }));
        return snapshot;
    } catch (error) {
        if (error?.name === 'AbortError') {
            return null;
        }
        showMainSessionLoadFailed(safeSessionId);
        sysLog(`Failed to return to main session: ${error.message || error}`, 'log-error');
        return null;
    } finally {
        clearMainSessionRestoreController(restoreController);
    }
}

export function abortMainSessionRestore() {
    if (!mainSessionRestoreController) {
        return;
    }
    mainSessionRestoreController.abort();
    mainSessionRestoreController = null;
    mainSessionRestoreToken += 1;
}

function resetMainSessionRestoreController() {
    abortMainSessionRestore();
    mainSessionRestoreController = new AbortController();
    mainSessionRestoreToken += 1;
    return {
        controller: mainSessionRestoreController,
        token: mainSessionRestoreToken,
    };
}

function clearMainSessionRestoreController(controller) {
    if (mainSessionRestoreController === controller) {
        mainSessionRestoreController = null;
    }
}

function isLatestMainSessionRestore(token, controller, sessionId) {
    return !!(
        mainSessionRestoreController === controller
        && mainSessionRestoreToken === token
        && !controller.signal.aborted
        && String(state.currentSessionId || '').trim() === String(sessionId || '').trim()
    );
}

function showMainSessionLoadingPlaceholder(sessionId) {
    if (!els.chatMessages) {
        return;
    }
    els.chatMessages.innerHTML = `
        <div class="subagent-main-session-loading" data-session-id="${escapeAttribute(sessionId)}" role="status" aria-live="polite">
            <span class="subagent-main-session-loading-spinner" aria-hidden="true"></span>
            <span>${escapeHtml(t('session.loading'))}</span>
        </div>
    `;
}

function showMainSessionLoadFailed(sessionId) {
    if (!els.chatMessages) {
        return;
    }
    els.chatMessages.innerHTML = `
        <div class="subagent-main-session-loading is-error" data-session-id="${escapeAttribute(sessionId)}" role="status">
            <span>${escapeHtml(t('subagent_session.load_failed'))}</span>
        </div>
    `;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value);
}
