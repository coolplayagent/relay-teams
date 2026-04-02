/**
 * components/settings/githubSettings.js
 * GitHub CLI settings persistence and connectivity checks.
 */
import {
    fetchGitHubConfig,
    probeGitHubConnectivity,
    saveGitHubConfig,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const MASKED_SECRET_PLACEHOLDER = '************';

let lastProbeState = null;
let languageBound = false;
let githubTokenState = createGitHubTokenState();

export function bindGitHubSettingsHandlers() {
    const saveBtn = document.getElementById('save-github-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveGitHub;
    }

    const probeBtn = document.getElementById('test-github-btn');
    if (probeBtn) {
        probeBtn.onclick = handleProbeGitHub;
    }

    const tokenInput = document.getElementById('github-token');
    if (tokenInput) {
        tokenInput.oninput = handleGitHubTokenInput;
        tokenInput.onchange = handleGitHubTokenInput;
    }

    const toggleTokenBtn = document.getElementById('toggle-github-token-btn');
    if (toggleTokenBtn) {
        toggleTokenBtn.onclick = toggleGitHubTokenVisibility;
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderGitHubTokenField();
            renderGitHubProbeState();
        });
        languageBound = true;
    }
}

export async function loadGitHubSettingsPanel() {
    try {
        const config = await fetchGitHubConfig();
        writeGitHubFormValues(config);
        renderGitHubProbeState();
    } catch (e) {
        logError(
            'frontend.github_settings.load_failed',
            'Failed to load GitHub config',
            errorToPayload(e),
        );
        showToast({
            title: t('settings.github.load_failed'),
            message: formatMessage('settings.github.load_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleSaveGitHub() {
    try {
        await saveGitHubConfig(readGitHubFormValues());
        showToast({
            title: t('settings.github.saved'),
            message: t('settings.github.saved_message'),
            tone: 'success',
        });
        await loadGitHubSettingsPanel();
    } catch (e) {
        showToast({
            title: t('settings.github.save_failed'),
            message: formatMessage('settings.github.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleProbeGitHub() {
    const token = readGitHubTokenValue();
    if (!token) {
        lastProbeState = {
            status: 'failed',
            message: t('settings.github.enter_token'),
        };
        renderGitHubProbeState();
        return;
    }

    lastProbeState = {
        status: 'probing',
        message: t('settings.github.testing_message'),
    };
    renderGitHubProbeState();

    try {
        const result = await probeGitHubConnectivity(readGitHubFormValues());
        lastProbeState = buildProbeState(result);
    } catch (e) {
        lastProbeState = {
            status: 'failed',
            message: formatMessage('settings.github.probe_failed', { error: e.message }),
        };
    }

    renderGitHubProbeState();
}

function buildProbeState(result) {
    if (result.ok) {
        const username = result.username || 'unknown';
        const version = result.gh_version ? `gh ${result.gh_version}` : 'gh';
        return {
            status: 'success',
            message: formatMessage('settings.github.probe_success', {
                username,
                version,
                latency_ms: result.latency_ms,
            }),
        };
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    const version = result.gh_version ? `gh ${result.gh_version}. ` : '';
    return {
        status: 'failed',
        message: formatMessage('settings.github.probe_reason', { version, reason }),
    };
}

function renderGitHubProbeState() {
    const statusEl = document.getElementById('github-probe-status');
    const probeBtn = document.getElementById('test-github-btn');
    if (!statusEl || !probeBtn) {
        return;
    }

    if (!lastProbeState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'proxy-probe-status';
        probeBtn.disabled = false;
        probeBtn.textContent = t('settings.github.test_connection');
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = lastProbeState.message;
    statusEl.className = `proxy-probe-status probe-status probe-status-${lastProbeState.status}`;
    probeBtn.disabled = lastProbeState.status === 'probing';
    probeBtn.textContent = lastProbeState.status === 'probing'
        ? t('settings.github.testing')
        : t('settings.github.test_connection');
}

function writeGitHubFormValues(config) {
    githubTokenState = createGitHubTokenState(config.token);
    renderGitHubTokenField();
}

function readGitHubFormValues() {
    return {
        token: readGitHubTokenValue(),
    };
}

function setInputValue(id, value) {
    const input = document.getElementById(id);
    if (!input) {
        return;
    }
    input.value = value || '';
}

function readInputValue(id) {
    const input = document.getElementById(id);
    if (!input) {
        return '';
    }
    return input.value.trim();
}

function createGitHubTokenState(persistedValue = null) {
    const normalizedValue = typeof persistedValue === 'string' ? persistedValue : '';
    return {
        persistedValue: normalizedValue,
        draftValue: '',
        hasPersistedValue: Boolean(normalizedValue.trim()),
        isDirty: false,
        revealed: false,
    };
}

function handleGitHubTokenInput() {
    const tokenInput = document.getElementById('github-token');
    const nextValue = tokenInput ? tokenInput.value : '';
    githubTokenState.draftValue = nextValue;
    githubTokenState.isDirty = githubTokenState.hasPersistedValue
        ? nextValue !== githubTokenState.persistedValue
        : nextValue.trim().length > 0;
    if (!readGitHubTokenValue()) {
        githubTokenState.revealed = false;
    }
    renderGitHubTokenField();
}

function toggleGitHubTokenVisibility() {
    if (!hasGitHubTokenValue()) {
        return;
    }
    githubTokenState.revealed = !githubTokenState.revealed;
    renderGitHubTokenField();
}

function readGitHubTokenValue() {
    const tokenInput = document.getElementById('github-token');
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (!githubTokenState.hasPersistedValue) {
        return inputValue || null;
    }
    if (githubTokenState.isDirty) {
        return inputValue || null;
    }
    return inputValue || githubTokenState.persistedValue || null;
}

function renderGitHubTokenField() {
    const tokenInput = document.getElementById('github-token');
    if (!tokenInput) {
        return;
    }

    if (githubTokenState.revealed) {
        tokenInput.type = 'text';
        tokenInput.value = githubTokenState.isDirty
            ? githubTokenState.draftValue
            : githubTokenState.persistedValue;
        tokenInput.placeholder = '';
    } else if (githubTokenState.hasPersistedValue && !githubTokenState.isDirty) {
        tokenInput.type = 'password';
        tokenInput.value = '';
        tokenInput.placeholder = MASKED_SECRET_PLACEHOLDER;
    } else {
        tokenInput.type = 'password';
        tokenInput.value = githubTokenState.draftValue;
        tokenInput.placeholder = t('settings.github.token_placeholder');
    }

    renderGitHubTokenToggle();
}

function renderGitHubTokenToggle() {
    const toggleTokenBtn = document.getElementById('toggle-github-token-btn');
    if (!toggleTokenBtn) {
        return;
    }

    toggleTokenBtn.style.display = hasGitHubTokenValue() ? 'inline-flex' : 'none';
    toggleTokenBtn.className = githubTokenState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    toggleTokenBtn.title = githubTokenState.revealed
        ? t('settings.github.hide_token')
        : t('settings.github.show_token');
    if (typeof toggleTokenBtn.setAttribute === 'function') {
        toggleTokenBtn.setAttribute('aria-label', toggleTokenBtn.title);
    } else {
        toggleTokenBtn.ariaLabel = toggleTokenBtn.title;
    }
}

function hasGitHubTokenValue() {
    const tokenInput = document.getElementById('github-token');
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (githubTokenState.hasPersistedValue && !githubTokenState.isDirty) {
        return Boolean(githubTokenState.persistedValue || inputValue);
    }
    return Boolean(githubTokenState.draftValue.trim() || inputValue);
}
