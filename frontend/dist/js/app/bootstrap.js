/**
 * app/bootstrap.js
 * UI bindings and application startup sequence.
 */
import { initSettings, openSettings } from '../components/settings.js';
import { initializeSubagentRail } from '../components/subagentRail.js';
import { handleNewSessionClick, loadSessions } from '../components/sidebar.js';
import { setupNavbarBindings } from '../components/navbar.js';
import { initBackendStatusMonitor } from '../utils/backendStatus.js';
import { initUiFeedback } from '../utils/feedback.js';
import { resumeRecoverableRun } from './recovery.js';
import { state } from '../core/state.js';
import { requestStopCurrentRun } from '../core/stream.js';
import { els } from '../utils/dom.js';
import {
    installGlobalErrorLogging,
    logInfo,
    sysLog,
} from '../utils/logger.js';

export function setupEventBindings(handleSend) {
    els.promptInput.addEventListener('input', () => {
        els.promptInput.style.height = 'auto';
        els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
    });
    els.promptInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            void handleSend();
        }
    });
    if (els.chatForm) {
        els.chatForm.addEventListener('submit', e => {
            e.preventDefault();
            void handleSend();
        });
    }
    if (els.stopBtn) {
        els.stopBtn.onclick = async () => {
            try {
                const requested = await requestStopCurrentRun();
                if (!requested) {
                    return;
                }
            } catch (e) {
                sysLog(`Stop failed: ${e.message}`, 'log-error');
            }
        };
    }
    if (els.newSessionBtn) els.newSessionBtn.onclick = () => handleNewSessionClick(true);
    document.addEventListener('run-approval-resolved', (event) => {
        const runId = event?.detail?.runId;
        if (!runId || typeof runId !== 'string') return;
        void resumeRecoverableRun(runId, {
            sessionId: state.currentSessionId,
            reason: 'tool approval resolved',
            quiet: true,
        });
    });
}

function setupSettingsButton() {
    const settingsBtn = document.getElementById('settings-btn');
    if (settingsBtn) {
        settingsBtn.onclick = openSettings;
    }
}

export async function initApp(selectSession, handleSend) {
    installGlobalErrorLogging();
    logInfo('frontend.bootstrap.started', 'Frontend bootstrap started');
    sysLog('System Initialized');
    initUiFeedback();
    initBackendStatusMonitor();
    setupNavbarBindings();
    initializeSubagentRail();
    setupEventBindings(handleSend);
    initSettings();
    setupSettingsButton();
    await loadSessions();

    const firstSessionEl = document.querySelector('.session-item .session-id');
    if (firstSessionEl) {
        await selectSession(firstSessionEl.textContent);
    } else {
        await handleNewSessionClick(false);
    }
}
