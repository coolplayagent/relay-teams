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
const DEFAULT_CLAWHUB_FIELD_IDS = Object.freeze({
    saveButtonId: 'save-clawhub-token-btn',
    probeButtonId: 'test-clawhub-btn',
    tokenInputId: 'clawhub-token',
    toggleTokenButtonId: 'toggle-clawhub-token-btn',
    statusId: 'clawhub-probe-status',
});

let lastProbeState = null;
let languageBound = false;
let clawhubTokenState = createClawHubTokenState();

export function bindClawHubSettingsHandlers(fieldIds = DEFAULT_CLAWHUB_FIELD_IDS) {
    const ids = resolveClawHubFieldIds(fieldIds);
    const saveBtn = document.getElementById(ids.saveButtonId);
    if (saveBtn) {
        saveBtn.onclick = () => {
            void handleSaveClawHubToken(ids);
        };
    }

    const probeBtn = document.getElementById(ids.probeButtonId);
    if (probeBtn) {
        probeBtn.onclick = () => {
            void handleProbeClawHub(ids);
        };
    }

    const tokenInput = document.getElementById(ids.tokenInputId);
    if (tokenInput) {
        tokenInput.oninput = () => {
            handleClawHubTokenInput(ids);
        };
        tokenInput.onchange = () => {
            handleClawHubTokenInput(ids);
        };
    }

    const toggleTokenBtn = document.getElementById(ids.toggleTokenButtonId);
    if (toggleTokenBtn) {
        toggleTokenBtn.onclick = () => {
            toggleClawHubTokenVisibility(ids);
        };
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderClawHubTokenField(ids);
            renderClawHubProbeState(ids);
        });
        languageBound = true;
    }
}

export async function loadClawHubSettingsPanel(fieldIds = DEFAULT_CLAWHUB_FIELD_IDS) {
    const ids = resolveClawHubFieldIds(fieldIds);
    try {
        const config = await fetchClawHubConfig();
        writeClawHubFormValues(config, ids);
        renderClawHubProbeState(ids);
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

function resolveClawHubFieldIds(fieldIds) {
    return {
        ...DEFAULT_CLAWHUB_FIELD_IDS,
        ...(fieldIds && typeof fieldIds === 'object' ? fieldIds : {}),
    };
}

async function handleSaveClawHubToken(fieldIds) {
    try {
        await saveClawHubConfig(readClawHubFormValues(fieldIds));
        showToast({
            title: t('settings.clawhub.saved'),
            message: t('settings.clawhub.saved_message'),
            tone: 'success',
        });
        await loadClawHubSettingsPanel(fieldIds);
    } catch (e) {
        showToast({
            title: t('settings.clawhub.save_failed'),
            message: formatMessage('settings.clawhub.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleProbeClawHub(fieldIds) {
    const token = readClawHubTokenValue(fieldIds);
    if (!token) {
        lastProbeState = {
            status: 'failed',
            message: t('settings.clawhub.enter_token'),
        };
        renderClawHubProbeState(fieldIds);
        return;
    }

    lastProbeState = {
        status: 'probing',
        message: t('settings.clawhub.testing_message'),
    };
    renderClawHubProbeState(fieldIds);

    try {
        const result = await probeClawHubConnectivity(readClawHubFormValues(fieldIds));
        lastProbeState = buildProbeState(result);
    } catch (e) {
        lastProbeState = {
            status: 'failed',
            message: formatMessage('settings.clawhub.probe_failed', { error: e.message }),
        };
    }

    renderClawHubProbeState(fieldIds);
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

function renderClawHubProbeState(fieldIds) {
    const statusEl = document.getElementById(fieldIds.statusId);
    const probeBtn = document.getElementById(fieldIds.probeButtonId);
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

function writeClawHubFormValues(config, fieldIds) {
    clawhubTokenState = createClawHubTokenState(config.token);
    renderClawHubTokenField(fieldIds);
}

function readClawHubFormValues(fieldIds) {
    return {
        token: readClawHubTokenValue(fieldIds),
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

function handleClawHubTokenInput(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const nextValue = tokenInput ? tokenInput.value : '';
    clawhubTokenState.draftValue = nextValue;
    clawhubTokenState.isDirty = clawhubTokenState.hasPersistedValue
        ? nextValue !== clawhubTokenState.persistedValue
        : nextValue.trim().length > 0;
    if (!readClawHubTokenValue(fieldIds)) {
        clawhubTokenState.revealed = false;
    }
    renderClawHubTokenField(fieldIds);
}

function toggleClawHubTokenVisibility(fieldIds) {
    if (!hasClawHubTokenValue(fieldIds)) {
        return;
    }
    clawhubTokenState.revealed = !clawhubTokenState.revealed;
    renderClawHubTokenField(fieldIds);
}

function readClawHubTokenValue(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (!clawhubTokenState.hasPersistedValue) {
        return inputValue || null;
    }
    if (clawhubTokenState.isDirty) {
        return inputValue || null;
    }
    return inputValue || clawhubTokenState.persistedValue || null;
}

function renderClawHubTokenField(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
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

    renderClawHubTokenToggle(fieldIds);
}

function renderClawHubTokenToggle(fieldIds) {
    const toggleTokenBtn = document.getElementById(fieldIds.toggleTokenButtonId);
    if (!toggleTokenBtn) {
        return;
    }

    toggleTokenBtn.style.display = hasClawHubTokenValue(fieldIds) ? 'inline-flex' : 'none';
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

function hasClawHubTokenValue(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (clawhubTokenState.hasPersistedValue && !clawhubTokenState.isDirty) {
        return Boolean(clawhubTokenState.persistedValue || inputValue);
    }
    return Boolean(clawhubTokenState.draftValue.trim() || inputValue);
}
