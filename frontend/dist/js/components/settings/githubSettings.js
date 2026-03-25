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
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let lastProbeState = null;
let languageBound = false;

export function bindGitHubSettingsHandlers() {
    const saveBtn = document.getElementById('save-github-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveGitHub;
    }

    const probeBtn = document.getElementById('test-github-btn');
    if (probeBtn) {
        probeBtn.onclick = handleProbeGitHub;
    }
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
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
            message: `Failed to load GitHub config: ${e.message}`,
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
            message: `Failed to save GitHub config: ${e.message}`,
            tone: 'danger',
        });
    }
}

async function handleProbeGitHub() {
    const token = readInputValue('github-token');
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
            message: `Probe failed: ${e.message}`,
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
            message: `${username} via ${version} in ${result.latency_ms}ms`,
        };
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    const version = result.gh_version ? `gh ${result.gh_version}. ` : '';
    return {
        status: 'failed',
        message: `${version}${reason}`,
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
    setInputValue('github-token', config.token);
}

function readGitHubFormValues() {
    return {
        token: readInputValue('github-token') || null,
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
