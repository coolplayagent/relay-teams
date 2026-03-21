/**
 * app/bootstrap.js
 * UI bindings and application startup sequence.
 */
import { initSettings, openSettings } from '../components/settings.js';
import { initializeContextIndicators } from '../components/contextIndicators.js';
import { initializeSessionTokenUsage } from '../components/sessionTokenUsage.js';
import { initializeSubagentRail } from '../components/subagentRail.js';
import {
    handleNewProjectClick,
    loadProjects,
    toggleProjectSortMode,
} from '../components/sidebar.js';
import { fetchRoleConfigOptions } from '../core/api.js';
import { setCoordinatorRoleId, setMainAgentRoleId, state } from '../core/state.js';
import { setupNavbarBindings } from '../components/navbar.js';
import { initBackendStatusMonitor } from '../utils/backendStatus.js';
import { initUiFeedback } from '../utils/feedback.js';
import { initializeLanguage, toggleLanguage } from '../utils/i18n.js';
import { resumeRecoverableRun } from './recovery.js';
import {
    initializeSessionTopologyControls,
    initializeThinkingControls,
    initializeYoloToggle,
} from './prompt.js';
import { requestStopCurrentRun } from '../core/stream.js';
import { els } from '../utils/dom.js';
import {
    errorToPayload,
    installGlobalErrorLogging,
    logInfo,
    logError,
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
    const languageToggleBtn = document.getElementById('language-toggle-btn');
    const settingsBtn = document.getElementById('settings-btn');
    if (languageToggleBtn) {
        languageToggleBtn.onclick = () => {
            void toggleLanguage();
        };
    }
    if (settingsBtn) {
        settingsBtn.onclick = openSettings;
    }
}

async function hydrateCoordinatorRoleId() {
    try {
        const options = await fetchRoleConfigOptions();
        setCoordinatorRoleId(options?.coordinator_role_id || '');
        setMainAgentRoleId(options?.main_agent_role_id || '');
    } catch (error) {
        logError(
            'frontend.bootstrap.coordinator_role_failed',
            'Failed to load coordinator role metadata',
            errorToPayload(error),
        );
        setCoordinatorRoleId('');
        setMainAgentRoleId('');
    }
}

export async function initApp(selectSession, handleSend) {
    installGlobalErrorLogging();
    logInfo('frontend.bootstrap.started', 'Frontend bootstrap started');
    await initializeLanguage();
    sysLog('System Initialized');
    initUiFeedback();
    initBackendStatusMonitor();
    setupNavbarBindings();
    initializeYoloToggle();
    initializeThinkingControls();
    await initializeSessionTopologyControls();
    initializeContextIndicators();
    initializeSessionTokenUsage();
    await hydrateCoordinatorRoleId();
    initializeSubagentRail();
    setupEventBindings(handleSend);
    initSettings();
    setupSettingsButton();
    await loadProjects();

    const firstSessionEl = document.querySelector('.session-item');
    if (firstSessionEl) {
        const sessionId = String(firstSessionEl.getAttribute('data-session-id') || '').trim();
        if (sessionId) {
            await selectSession(sessionId);
        }
    }
}
