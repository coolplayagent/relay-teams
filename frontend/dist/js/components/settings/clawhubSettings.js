/**
 * components/settings/clawhubSettings.js
 * ClawHub token persistence and CLI probe support for the Skills panel.
 */
import {
    fetchClawHubConfig,
    probeClawHubConnectivity,
    saveClawHubConfig,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const MASKED_SECRET_PLACEHOLDER = '************';

let lastProbeState = null;
let languageBound = false;
let clawhubTokenState = createClawHubTokenState();

export function bindClawHubSettingsHandlers() {
    const saveBtn = document.getElementById('save-clawhub-token-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveClawHubToken;
    }

    const probeBtn = document.getElementById('test-clawhub-btn');
    if (probeBtn) {
        probeBtn.onclick = handleProbeClawHub;
    }

    const tokenInput = document.getElementById('clawhub-token');
    if (tokenInput) {
        tokenInput.oninput = handleClawHubTokenInput;
        tokenInput.onchange = handleClawHubTokenInput;
    }

    const toggleTokenBtn = document.getElementById('toggle-clawhub-token-btn');
    if (toggleTokenBtn) {
        toggleTokenBtn.onclick = toggleClawHubTokenVisibility;
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderClawHubTokenField();
            renderClawHubProbeState();
        });
        languageBound = true;
    }
}

export async function loadClawHubSettingsPanel() {
    try {
        const config = await fetchClawHubConfig();
        writeClawHubFormValues(config);
        renderClawHubProbeState();
    } catch (e) {
        logError(
            'frontend.clawhub_settings.load_failed',
            'Failed to load ClawHub config',
            errorToPayload(e),
        );
        showToast({
            title: t('settings.clawhub.load_failed'),
            message: formatMessage('settings.clawhub.load_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleSaveClawHubToken() {
    try {
        await saveClawHubConfig(readClawHubFormValues());
        showToast({
            title: t('settings.clawhub.saved'),
            message: t('settings.clawhub.saved_message'),
            tone: 'success',
        });
        await loadClawHubSettingsPanel();
    } catch (e) {
        showToast({
            title: t('settings.clawhub.save_failed'),
            message: formatMessage('settings.clawhub.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleProbeClawHub() {
    const token = readClawHubTokenValue();
    if (!token) {
        lastProbeState = {
            status: 'failed',
            message: t('settings.clawhub.enter_token'),
        };
        renderClawHubProbeState();
        return;
    }

    lastProbeState = {
        status: 'probing',
        message: t('settings.clawhub.testing_message'),
    };
    renderClawHubProbeState();

    try {
        const result = await probeClawHubConnectivity(readClawHubFormValues());
        lastProbeState = buildProbeState(result);
    } catch (e) {
        lastProbeState = {
            status: 'failed',
            message: formatMessage('settings.clawhub.probe_failed', { error: e.message }),
        };
    }

    renderClawHubProbeState();
}

function buildProbeState(result) {
    if (result.ok) {
        const version = result.clawhub_version || 'clawhub';
        const probeMessageKey = result.diagnostics?.installed_during_probe
            ? 'settings.clawhub.probe_success_after_install'
            : 'settings.clawhub.probe_success';
        return {
            status: 'success',
            message: formatMessage(probeMessageKey, {
                version,
                latency_ms: result.latency_ms,
            }),
        };
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    return {
        status: 'failed',
        message: formatMessage('settings.clawhub.probe_reason', { reason }),
    };
}

function renderClawHubProbeState() {
    const statusEl = document.getElementById('clawhub-probe-status');
    const probeBtn = document.getElementById('test-clawhub-btn');
    if (!statusEl || !probeBtn) {
        return;
    }

    if (!lastProbeState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'proxy-probe-status';
        probeBtn.disabled = false;
        probeBtn.textContent = t('settings.clawhub.test_connection');
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = lastProbeState.message;
    statusEl.className = `proxy-probe-status probe-status probe-status-${lastProbeState.status}`;
    probeBtn.disabled = lastProbeState.status === 'probing';
    probeBtn.textContent = lastProbeState.status === 'probing'
        ? t('settings.clawhub.testing')
        : t('settings.clawhub.test_connection');
}

function writeClawHubFormValues(config) {
    clawhubTokenState = createClawHubTokenState(config.token);
    renderClawHubTokenField();
}

function readClawHubFormValues() {
    return {
        token: readClawHubTokenValue(),
    };
}

function createClawHubTokenState(persistedValue = null) {
    const normalizedValue = typeof persistedValue === 'string' ? persistedValue : '';
    return {
        persistedValue: normalizedValue,
        draftValue: '',
        hasPersistedValue: Boolean(normalizedValue.trim()),
        isDirty: false,
        revealed: false,
    };
}

function handleClawHubTokenInput() {
    const tokenInput = document.getElementById('clawhub-token');
    const nextValue = tokenInput ? tokenInput.value : '';
    clawhubTokenState.draftValue = nextValue;
    clawhubTokenState.isDirty = clawhubTokenState.hasPersistedValue
        ? nextValue !== clawhubTokenState.persistedValue
        : nextValue.trim().length > 0;
    if (!readClawHubTokenValue()) {
        clawhubTokenState.revealed = false;
    }
    renderClawHubTokenField();
}

function toggleClawHubTokenVisibility() {
    if (!hasClawHubTokenValue()) {
        return;
    }
    clawhubTokenState.revealed = !clawhubTokenState.revealed;
    renderClawHubTokenField();
}

function readClawHubTokenValue() {
    const tokenInput = document.getElementById('clawhub-token');
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (!clawhubTokenState.hasPersistedValue) {
        return inputValue || null;
    }
    if (clawhubTokenState.isDirty) {
        return inputValue || null;
    }
    return inputValue || clawhubTokenState.persistedValue || null;
}

function renderClawHubTokenField() {
    const tokenInput = document.getElementById('clawhub-token');
    if (!tokenInput) {
        return;
    }

    if (clawhubTokenState.revealed) {
        tokenInput.type = 'text';
        tokenInput.value = clawhubTokenState.isDirty
            ? clawhubTokenState.draftValue
            : clawhubTokenState.persistedValue;
        tokenInput.placeholder = '';
    } else if (clawhubTokenState.hasPersistedValue && !clawhubTokenState.isDirty) {
        tokenInput.type = 'password';
        tokenInput.value = '';
        tokenInput.placeholder = MASKED_SECRET_PLACEHOLDER;
    } else {
        tokenInput.type = 'password';
        tokenInput.value = clawhubTokenState.draftValue;
        tokenInput.placeholder = t('settings.clawhub.token_placeholder');
    }

    renderClawHubTokenToggle();
}

function renderClawHubTokenToggle() {
    const toggleTokenBtn = document.getElementById('toggle-clawhub-token-btn');
    if (!toggleTokenBtn) {
        return;
    }

    toggleTokenBtn.style.display = hasClawHubTokenValue() ? 'inline-flex' : 'none';
    toggleTokenBtn.className = clawhubTokenState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    toggleTokenBtn.title = clawhubTokenState.revealed
        ? t('settings.clawhub.hide_token')
        : t('settings.clawhub.show_token');
    if (typeof toggleTokenBtn.setAttribute === 'function') {
        toggleTokenBtn.setAttribute('aria-label', toggleTokenBtn.title);
    } else {
        toggleTokenBtn.ariaLabel = toggleTokenBtn.title;
    }
}

function hasClawHubTokenValue() {
    const tokenInput = document.getElementById('clawhub-token');
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (clawhubTokenState.hasPersistedValue && !clawhubTokenState.isDirty) {
        return Boolean(clawhubTokenState.persistedValue || inputValue);
    }
    return Boolean(clawhubTokenState.draftValue.trim() || inputValue);
}
