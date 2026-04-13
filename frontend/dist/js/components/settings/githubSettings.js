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
export const DEFAULT_GITHUB_FIELD_IDS = Object.freeze({
    saveButtonId: 'save-github-btn',
    probeButtonId: 'test-github-btn',
    tokenInputId: 'github-token',
    toggleTokenButtonId: 'toggle-github-token-btn',
    statusId: 'github-probe-status',
});

let lastProbeState = null;
let languageBound = false;
let githubTokenState = createGitHubTokenState();

export function bindGitHubSettingsHandlers(fieldIds = DEFAULT_GITHUB_FIELD_IDS) {
    const ids = resolveGitHubFieldIds(fieldIds);
    const saveBtn = document.getElementById(ids.saveButtonId);
    if (saveBtn) {
        saveBtn.onclick = () => handleSaveGitHub(ids);
    }

    const probeBtn = document.getElementById(ids.probeButtonId);
    if (probeBtn) {
        probeBtn.onclick = () => handleProbeGitHub(ids);
    }

    const tokenInput = document.getElementById(ids.tokenInputId);
    if (tokenInput) {
        tokenInput.oninput = () => {
            handleGitHubTokenInput(ids);
        };
        tokenInput.onchange = () => {
            handleGitHubTokenInput(ids);
        };
    }

    const toggleTokenBtn = document.getElementById(ids.toggleTokenButtonId);
    if (toggleTokenBtn) {
        toggleTokenBtn.onclick = () => {
            toggleGitHubTokenVisibility(ids);
        };
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderGitHubTokenField(ids);
            renderGitHubProbeState(ids);
        });
        languageBound = true;
    }
}

export async function loadGitHubSettingsPanel(fieldIds = DEFAULT_GITHUB_FIELD_IDS) {
    const ids = resolveGitHubFieldIds(fieldIds);
    try {
        const config = await fetchGitHubConfig();
        writeGitHubFormValues(config, ids);
        renderGitHubProbeState(ids);
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

export function renderGitHubAccessPanelMarkup(
    fieldIds = DEFAULT_GITHUB_FIELD_IDS,
) {
    const ids = resolveGitHubFieldIds(fieldIds);
    return `
        <div class="proxy-editor-form">
            <section class="proxy-form-section">
                <div class="proxy-form-section-header">
                    <h5 data-i18n="settings.github.section">GitHub CLI</h5>
                </div>
                <div class="proxy-form-grid">
                    <div class="form-group proxy-inline-field">
                        <label for="${escapeHtml(ids.tokenInputId)}" data-i18n="settings.github.token">GitHub Token</label>
                        <div class="secure-input-row">
                            <input type="password" id="${escapeHtml(ids.tokenInputId)}" placeholder="ghp_..." data-i18n-placeholder="settings.github.token_placeholder" autocomplete="current-password">
                            <button class="secure-input-btn" id="${escapeHtml(ids.toggleTokenButtonId)}" type="button" title="Show GitHub token" aria-label="Show GitHub token" style="display:none;">
                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                    <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                </svg>
                            </button>
                        </div>
                    </div>
                    <div class="form-group proxy-inline-field web-provider-inline-field">
                        <span class="settings-token-source-label" data-i18n="settings.github.token_source">Get token</span>
                        <a class="web-provider-link-card" id="github-token-link" href="https://github.com/settings/tokens" target="_blank" rel="noreferrer" title="https://github.com/settings/tokens" aria-label="https://github.com/settings/tokens">
                            <span class="web-provider-link-copy">
                                <span class="web-provider-link-badge">GitHub</span>
                                <span class="web-provider-link-url">https://github.com/settings/tokens</span>
                                <span class="settings-token-source-note" data-i18n="settings.github.token_source_help">Open GitHub token settings to create or copy a token</span>
                            </span>
                            <span class="web-provider-link-arrow" aria-hidden="true">
                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                    <path d="M7 17L17 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                    <path d="M9 7h8v8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                </svg>
                            </span>
                        </a>
                    </div>
                    <div class="form-group proxy-inline-field proxy-inline-field-actions">
                        <label for="${escapeHtml(ids.probeButtonId)}" data-i18n="settings.github.token_action">Token Actions</label>
                        <div class="settings-inline-action-row">
                            <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.probeButtonId)}" type="button" data-i18n="settings.github.test_connection">Test Connection</button>
                            <button class="primary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.saveButtonId)}" type="button" data-i18n="settings.action.save">Save</button>
                        </div>
                    </div>
                </div>
                <div class="proxy-probe-status" id="${escapeHtml(ids.statusId)}" style="display:none;"></div>
            </section>
        </div>
    `;
}

function resolveGitHubFieldIds(fieldIds) {
    return {
        ...DEFAULT_GITHUB_FIELD_IDS,
        ...(fieldIds && typeof fieldIds === 'object' ? fieldIds : {}),
    };
}

async function handleSaveGitHub(fieldIds) {
    try {
        await saveGitHubConfig(readGitHubFormValues(fieldIds));
        showToast({
            title: t('settings.github.saved'),
            message: t('settings.github.saved_message'),
            tone: 'success',
        });
        await loadGitHubSettingsPanel(fieldIds);
    } catch (e) {
        showToast({
            title: t('settings.github.save_failed'),
            message: formatMessage('settings.github.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleProbeGitHub(fieldIds) {
    lastProbeState = {
        status: 'probing',
        message: t('settings.github.testing_message'),
    };
    renderGitHubProbeState(fieldIds);

    const token = readGitHubTokenValue(fieldIds);
    const probeMessages = [];
    let hasFailure = false;

    if (!token && !hasPersistedGitHubToken()) {
        hasFailure = true;
        probeMessages.push(t('settings.github.enter_token'));
    } else {
        try {
            const payload = token ? { token } : {};
            const result = await probeGitHubConnectivity(payload);
            probeMessages.push(buildGitHubTokenProbeMessage(result));
            if (!result.ok) {
                hasFailure = true;
            }
        } catch (e) {
            hasFailure = true;
            probeMessages.push(formatMessage('settings.github.probe_failed', { error: e.message }));
        }
    }

    lastProbeState = {
        status: hasFailure ? 'failed' : 'success',
        message: probeMessages.join(' '),
    };

    renderGitHubProbeState(fieldIds);
}

function buildGitHubTokenProbeMessage(result) {
    if (result.ok) {
        const username = result.username || 'unknown';
        const version = result.gh_version ? `gh ${result.gh_version}` : 'gh';
        return formatMessage('settings.github.probe_success', {
            username,
            version,
            latency_ms: result.latency_ms,
        });
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    const version = result.gh_version ? `gh ${result.gh_version}. ` : '';
    return formatMessage('settings.github.probe_reason', { version, reason });
}

function renderGitHubProbeState(fieldIds) {
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

function writeGitHubFormValues(config, fieldIds) {
    githubTokenState = createGitHubTokenState(config.token_configured === true);
    renderGitHubTokenField(fieldIds);
}

function readGitHubFormValues(fieldIds) {
    const payload = {};
    const token = readGitHubTokenValue(fieldIds);
    if (token) {
        payload.token = token;
    }
    return payload;
}

function createGitHubTokenState(hasPersistedValue = false) {
    return {
        draftValue: '',
        hasPersistedValue: hasPersistedValue === true,
        isDirty: false,
        revealed: false,
    };
}

function handleGitHubTokenInput(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const nextValue = tokenInput ? tokenInput.value : '';
    githubTokenState.draftValue = nextValue;
    githubTokenState.isDirty = nextValue.trim().length > 0;
    if (!readGitHubTokenValue(fieldIds)) {
        githubTokenState.revealed = false;
    }
    renderGitHubTokenField(fieldIds);
}

function toggleGitHubTokenVisibility(fieldIds) {
    if (!hasGitHubTokenValue(fieldIds)) {
        return;
    }
    githubTokenState.revealed = !githubTokenState.revealed;
    renderGitHubTokenField(fieldIds);
}

function readGitHubTokenValue(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    return inputValue || null;
}

function renderGitHubTokenField(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    if (!tokenInput) {
        return;
    }

    if (githubTokenState.revealed) {
        tokenInput.type = 'text';
        tokenInput.value = githubTokenState.draftValue;
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

    renderGitHubTokenToggle(fieldIds);
}

function renderGitHubTokenToggle(fieldIds) {
    const toggleTokenBtn = document.getElementById(fieldIds.toggleTokenButtonId);
    if (!toggleTokenBtn) {
        return;
    }

    toggleTokenBtn.style.display = hasGitHubTokenValue(fieldIds) ? 'inline-flex' : 'none';
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

function hasGitHubTokenValue(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    return Boolean(githubTokenState.draftValue.trim() || inputValue);
}

function hasPersistedGitHubToken() {
    return githubTokenState.hasPersistedValue;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
