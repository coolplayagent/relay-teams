/**
 * components/settings/githubSettings.js
 * GitHub CLI settings persistence and connectivity checks.
 */
import {
    fetchGitHubConfig,
    fetchGitHubWebhookTunnelStatus,
    probeGitHubConnectivity,
    probeGitHubWebhookConnectivity,
    revealGitHubToken,
    saveGitHubConfig,
    startGitHubWebhookTunnel,
    stopGitHubWebhookTunnel,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const MASKED_SECRET_PLACEHOLDER = '************';
export const DEFAULT_GITHUB_FIELD_IDS = Object.freeze({
    saveButtonId: 'save-github-btn',
    probeButtonId: 'test-github-btn',
    tokenInputId: 'github-token',
    webhookSaveButtonId: 'save-github-webhook-btn',
    webhookProbeButtonId: 'test-github-webhook-btn',
    webhookBaseUrlInputId: 'github-webhook-base-url',
    callbackPreviewId: 'github-callback-preview',
    tunnelStartButtonId: 'start-github-webhook-tunnel-btn',
    tunnelStopButtonId: 'stop-github-webhook-tunnel-btn',
    tunnelStatusId: 'github-webhook-tunnel-status',
    toggleTokenButtonId: 'toggle-github-token-btn',
    statusId: 'github-probe-status',
    webhookStatusId: 'github-webhook-probe-status',
});

let lastProbeState = null;
let lastWebhookProbeState = null;
let lastTunnelStatus = null;
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

    const webhookSaveBtn = document.getElementById(ids.webhookSaveButtonId);
    if (webhookSaveBtn) {
        webhookSaveBtn.onclick = () => handleSaveGitHubWebhook(ids);
    }

    const webhookProbeBtn = document.getElementById(ids.webhookProbeButtonId);
    if (webhookProbeBtn) {
        webhookProbeBtn.onclick = () => handleProbeGitHubWebhook(ids);
    }

    const tunnelStartBtn = document.getElementById(ids.tunnelStartButtonId);
    if (tunnelStartBtn) {
        tunnelStartBtn.onclick = () => handleStartGitHubWebhookTunnel(ids);
    }

    const tunnelStopBtn = document.getElementById(ids.tunnelStopButtonId);
    if (tunnelStopBtn) {
        tunnelStopBtn.onclick = () => handleStopGitHubWebhookTunnel(ids);
    }

    const tokenInput = document.getElementById(ids.tokenInputId);
    if (tokenInput) {
        tokenInput.oninput = () => {
            handleGitHubTokenInput(ids);
        };
        tokenInput.onchange = () => {
            handleGitHubTokenInput(ids);
        };
        tokenInput.onfocus = () => {
            armGitHubTokenInput();
        };
        tokenInput.onpointerdown = () => {
            armGitHubTokenInput();
        };
        tokenInput.onkeydown = () => {
            armGitHubTokenInput();
        };
        tokenInput.onblur = () => {
            disarmGitHubTokenInput();
        };
    }

    const webhookBaseUrlInput = document.getElementById(ids.webhookBaseUrlInputId);
    if (webhookBaseUrlInput) {
        webhookBaseUrlInput.oninput = () => {
            renderGitHubCallbackPreview(ids);
        };
        webhookBaseUrlInput.onchange = () => {
            renderGitHubCallbackPreview(ids);
        };
    }

    const toggleTokenBtn = document.getElementById(ids.toggleTokenButtonId);
    if (toggleTokenBtn) {
        toggleTokenBtn.onclick = () => {
            void toggleGitHubTokenVisibility(ids);
        };
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderGitHubTokenField(ids);
            renderGitHubCallbackPreview(ids);
            renderGitHubTunnelHelp();
            renderGitHubProbeState(ids);
            renderGitHubWebhookProbeState(ids);
            renderGitHubWebhookTunnelStatus(ids);
        });
        languageBound = true;
    }
}

export async function loadGitHubSettingsPanel(fieldIds = DEFAULT_GITHUB_FIELD_IDS) {
    const ids = resolveGitHubFieldIds(fieldIds);
    try {
        const [config, tunnelStatus] = await Promise.all([
            fetchGitHubConfig(),
            fetchGitHubWebhookTunnelStatus().catch(() => null),
        ]);
        writeGitHubFormValues(config, ids);
        lastTunnelStatus = tunnelStatus;
        renderGitHubTunnelHelp();
        renderGitHubProbeState(ids);
        renderGitHubWebhookProbeState(ids);
        renderGitHubWebhookTunnelStatus(ids);
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
                            <input type="password" id="${escapeHtml(ids.tokenInputId)}" placeholder="ghp_..." data-i18n-placeholder="settings.github.token_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
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
                            <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.probeButtonId)}" type="button" data-i18n="settings.github.test_connection">${escapeHtml(t('settings.github.test_connection'))}</button>
                            <button class="primary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.saveButtonId)}" type="button" data-i18n="settings.action.save">${escapeHtml(t('settings.action.save'))}</button>
                        </div>
                    </div>
                </div>
                <div class="proxy-probe-status" id="${escapeHtml(ids.statusId)}" style="display:none;"></div>
            </section>
            <section class="proxy-form-section">
                <div class="proxy-form-section-header">
                    <h5 data-i18n="settings.github.webhook_section">GitHub Webhook</h5>
                </div>
                <div class="proxy-form-grid">
                    <div class="form-group proxy-inline-field">
                        <label for="${escapeHtml(ids.webhookBaseUrlInputId)}" data-i18n="settings.github.webhook_base_url">Webhook Base URL</label>
                        <input type="url" id="${escapeHtml(ids.webhookBaseUrlInputId)}" placeholder="https://agent-teams.example.com" data-i18n-placeholder="settings.github.webhook_base_url_placeholder" autocomplete="url" inputmode="url" spellcheck="false">
                    </div>
                    <div class="form-group proxy-inline-field">
                        <label for="${escapeHtml(ids.callbackPreviewId)}" data-i18n="settings.github.callback_url">Callback URL</label>
                        <code id="${escapeHtml(ids.callbackPreviewId)}" class="web-provider-link-url" aria-live="polite">${escapeHtml(t('settings.github.callback_preview_empty'))}</code>
                    </div>
                    <div class="form-group proxy-inline-field proxy-inline-field-actions">
                        <label for="${escapeHtml(ids.tunnelStartButtonId)}" data-i18n="settings.github.tunnel_actions">Temporary Public URL</label>
                        <div class="settings-inline-action-row">
                            <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.tunnelStartButtonId)}" type="button" data-i18n="settings.github.tunnel_start">${escapeHtml(t('settings.github.tunnel_start'))}</button>
                            <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.tunnelStopButtonId)}" type="button" data-i18n="settings.github.tunnel_stop">${escapeHtml(t('settings.github.tunnel_stop'))}</button>
                        </div>
                        <span class="settings-token-source-note github-webhook-tunnel-note" data-i18n="settings.github.tunnel_help" title="${escapeHtml(t('settings.github.tunnel_help'))}">${escapeHtml(t('settings.github.tunnel_help'))}</span>
                    </div>
                    <div class="form-group proxy-inline-field proxy-inline-field-actions">
                        <label for="${escapeHtml(ids.webhookProbeButtonId)}" data-i18n="settings.github.webhook_action">Webhook Actions</label>
                        <div class="settings-inline-action-row">
                            <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.webhookProbeButtonId)}" type="button" data-i18n="settings.github.test_webhook">${escapeHtml(t('settings.github.test_webhook'))}</button>
                            <button class="primary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(ids.webhookSaveButtonId)}" type="button" data-i18n="settings.action.save">${escapeHtml(t('settings.action.save'))}</button>
                        </div>
                    </div>
                </div>
                <div class="proxy-probe-status" id="${escapeHtml(ids.tunnelStatusId)}" style="display:none;"></div>
                <div class="proxy-probe-status" id="${escapeHtml(ids.webhookStatusId)}" style="display:none;"></div>
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
        const payload = readGitHubTokenFormValues(fieldIds);
        await saveGitHubConfig(payload);
        githubTokenState = createGitHubTokenState(
            payload.token || null,
            hasPersistedGitHubToken() || Boolean(readGitHubTokenValue(fieldIds)),
        );
        renderGitHubTokenField(fieldIds);
        showToast({
            title: t('settings.github.saved'),
            message: t('settings.github.saved_message'),
            tone: 'success',
        });
    } catch (e) {
        showToast({
            title: t('settings.github.save_failed'),
            message: formatMessage('settings.github.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleSaveGitHubWebhook(fieldIds) {
    try {
        await saveGitHubConfig(readGitHubWebhookFormValues(fieldIds));
        renderGitHubCallbackPreview(fieldIds);
        showToast({
            title: t('settings.github.webhook_saved'),
            message: t('settings.github.webhook_saved_message'),
            tone: 'success',
        });
    } catch (e) {
        showToast({
            title: t('settings.github.webhook_save_failed'),
            message: formatMessage('settings.github.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleStartGitHubWebhookTunnel(fieldIds) {
    lastTunnelStatus = {
        status: 'starting',
        last_message: t('settings.github.tunnel_starting'),
    };
    renderGitHubWebhookTunnelStatus(fieldIds);
    try {
        const startStatus = await startGitHubWebhookTunnel({
            auto_save_webhook_base_url: true,
        });
        const resolvedStatus = await resolveGitHubWebhookTunnelStartStatus(startStatus);
        lastTunnelStatus = resolvedStatus;
        const publicUrl = normalizeGitHubWebhookBaseUrl(resolvedStatus?.public_url);
        if (publicUrl) {
            const webhookBaseUrlInput = document.getElementById(fieldIds.webhookBaseUrlInputId);
            if (webhookBaseUrlInput) {
                webhookBaseUrlInput.value = publicUrl;
            }
            renderGitHubCallbackPreview(fieldIds);
            await saveGitHubConfig({ webhook_base_url: publicUrl });
        }
        showToast({
            title: t('settings.github.tunnel_started'),
            message: buildGitHubWebhookTunnelMessage(resolvedStatus),
            tone: resolvedStatus.status === 'failed' ? 'danger' : 'success',
        });
        await loadGitHubSettingsPanel(fieldIds);
    } catch (e) {
        lastTunnelStatus = {
            status: 'failed',
            error_message: e.message,
            last_message: e.message,
        };
        renderGitHubWebhookTunnelStatus(fieldIds);
        showToast({
            title: t('settings.github.tunnel_start_failed'),
            message: formatMessage('settings.github.tunnel_failed', { reason: e.message }),
            tone: 'danger',
        });
    }
}

async function resolveGitHubWebhookTunnelStartStatus(status) {
    let nextStatus = status;
    if (
        normalizeGitHubWebhookBaseUrl(nextStatus?.public_url)
        || nextStatus?.status === 'failed'
    ) {
        return nextStatus;
    }

    for (let attempt = 0; attempt < 5; attempt += 1) {
        await waitForGitHubWebhookTunnelStatus(250);
        try {
            nextStatus = await fetchGitHubWebhookTunnelStatus();
        } catch (_error) {
            break;
        }
        if (
            normalizeGitHubWebhookBaseUrl(nextStatus?.public_url)
            || nextStatus?.status === 'failed'
        ) {
            return nextStatus;
        }
    }
    return nextStatus;
}

function waitForGitHubWebhookTunnelStatus(delayMs) {
    return new Promise(resolve => {
        setTimeout(resolve, delayMs);
    });
}

async function handleStopGitHubWebhookTunnel(fieldIds) {
    try {
        const status = await stopGitHubWebhookTunnel({
            clear_webhook_base_url_if_matching: true,
        });
        lastTunnelStatus = status;
        await loadGitHubSettingsPanel(fieldIds);
        showToast({
            title: t('settings.github.tunnel_stopped'),
            message: buildGitHubWebhookTunnelMessage(status),
            tone: 'success',
        });
    } catch (e) {
        showToast({
            title: t('settings.github.tunnel_stop_failed'),
            message: formatMessage('settings.github.tunnel_failed', { reason: e.message }),
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

async function handleProbeGitHubWebhook(fieldIds) {
    lastWebhookProbeState = {
        status: 'probing',
        message: t('settings.github.webhook_testing_message'),
    };
    renderGitHubWebhookProbeState(fieldIds);

    const webhookBaseUrl = readGitHubWebhookBaseUrl(fieldIds);
    if (!webhookBaseUrl) {
        lastWebhookProbeState = {
            status: 'failed',
            message: t('settings.github.webhook_base_url_required'),
        };
        renderGitHubWebhookProbeState(fieldIds);
        return;
    }

    try {
        const result = await probeGitHubWebhookConnectivity({
            webhook_base_url: webhookBaseUrl,
        });
        lastWebhookProbeState = {
            status: result.ok ? 'success' : 'failed',
            message: buildGitHubWebhookProbeMessage(result),
        };
    } catch (e) {
        lastWebhookProbeState = {
            status: 'failed',
            message: formatMessage('settings.github.webhook_probe_failed', { error: e.message }),
        };
    }

    renderGitHubWebhookProbeState(fieldIds);
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

function buildGitHubWebhookProbeMessage(result) {
    const finalUrl = result.final_url || result.health_url || result.callback_url || 'unknown';
    if (result.ok) {
        return formatMessage('settings.github.webhook_probe_success', {
            status_code: result.status_code,
            latency_ms: result.latency_ms,
            final_url: finalUrl,
        });
    }

    const reason = resolveGitHubWebhookProbeReason(result);
    return formatMessage('settings.github.webhook_probe_reason', {
        final_url: finalUrl,
        reason,
    });
}

function resolveGitHubWebhookProbeReason(result) {
    if (result?.error_code === 'temporary_public_url_inactive') {
        return t('settings.github.webhook_probe_temporary_public_url_inactive');
    }
    return result.error_message || result.error_code || 'Unknown error';
}

function renderGitHubProbeState(fieldIds) {
    renderProbeState({
        statusId: fieldIds.statusId,
        probeButtonId: fieldIds.probeButtonId,
        state: lastProbeState,
        idleLabel: t('settings.github.test_connection'),
        busyLabel: t('settings.github.testing'),
    });
}

function renderGitHubWebhookProbeState(fieldIds) {
    renderProbeState({
        statusId: fieldIds.webhookStatusId,
        probeButtonId: fieldIds.webhookProbeButtonId,
        state: lastWebhookProbeState,
        idleLabel: t('settings.github.test_webhook'),
        busyLabel: t('settings.github.testing'),
    });
}

function renderGitHubWebhookTunnelStatus(fieldIds) {
    const statusEl = document.getElementById(fieldIds.tunnelStatusId);
    const startBtn = document.getElementById(fieldIds.tunnelStartButtonId);
    const stopBtn = document.getElementById(fieldIds.tunnelStopButtonId);
    if (!statusEl || !startBtn || !stopBtn) {
        return;
    }

    const tunnelStatus = lastTunnelStatus;
    const statusValue = tunnelStatus?.status || 'idle';
    statusEl.style.display = 'block';
    statusEl.textContent = buildGitHubWebhookTunnelMessage(tunnelStatus);
    let classSuffix = '';
    if (statusValue === 'active') {
        classSuffix = 'success';
    } else if (statusValue === 'failed') {
        classSuffix = 'failed';
    } else if (statusValue === 'starting') {
        classSuffix = 'probing';
    }
    statusEl.className = classSuffix
        ? `proxy-probe-status probe-status probe-status-${classSuffix}`
        : 'proxy-probe-status';

    startBtn.disabled = statusValue === 'starting' || statusValue === 'active';
    stopBtn.disabled = !(statusValue === 'starting' || statusValue === 'active');
}

function renderProbeState({
    statusId,
    probeButtonId,
    state,
    idleLabel,
    busyLabel,
}) {
    const statusEl = document.getElementById(statusId);
    const probeBtn = document.getElementById(probeButtonId);
    if (!statusEl || !probeBtn) {
        return;
    }

    if (!state) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'proxy-probe-status';
        probeBtn.disabled = false;
        probeBtn.textContent = idleLabel;
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = state.message;
    statusEl.className = `proxy-probe-status probe-status probe-status-${state.status}`;
    probeBtn.disabled = state.status === 'probing';
    probeBtn.textContent = state.status === 'probing' ? busyLabel : idleLabel;
}

function writeGitHubFormValues(config, fieldIds) {
    githubTokenState = createGitHubTokenState(
        null,
        config?.token_configured === true,
    );
    const webhookBaseUrlInput = document.getElementById(fieldIds.webhookBaseUrlInputId);
    if (webhookBaseUrlInput) {
        webhookBaseUrlInput.value = normalizeGitHubWebhookBaseUrl(config.webhook_base_url) || '';
    }
    renderGitHubTokenField(fieldIds);
    renderGitHubCallbackPreview(fieldIds);
}

function readGitHubTokenFormValues(fieldIds) {
    const payload = {};
    const token = readGitHubTokenValue(fieldIds);
    if (token) {
        payload.token = token;
    }
    return payload;
}

function readGitHubWebhookFormValues(fieldIds) {
    const payload = {};
    payload.webhook_base_url = readGitHubWebhookBaseUrl(fieldIds);
    return payload;
}

function createGitHubTokenState(persistedValue = null, hasPersistedValue = false) {
    const normalizedValue = typeof persistedValue === 'string' ? persistedValue : '';
    return {
        persistedValue: normalizedValue,
        persistedValueLoaded: Boolean(normalizedValue.trim()),
        draftValue: '',
        hasPersistedValue: hasPersistedValue === true || Boolean(normalizedValue.trim()),
        isDirty: false,
        isLoadingReveal: false,
        armedForInput: false,
        revealed: false,
    };
}

function handleGitHubTokenInput(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const nextValue = tokenInput ? tokenInput.value : '';
    if (
        githubTokenState.hasPersistedValue
        && !githubTokenState.persistedValueLoaded
        && !githubTokenState.revealed
        && !canAcceptGitHubTokenInput(tokenInput)
    ) {
        githubTokenState.draftValue = '';
        githubTokenState.isDirty = false;
        githubTokenState.armedForInput = false;
        githubTokenState.revealed = false;
        renderGitHubTokenField(fieldIds);
        return;
    }
    githubTokenState.draftValue = nextValue;
    githubTokenState.isDirty = githubTokenState.hasPersistedValue
        ? githubTokenState.persistedValueLoaded
            ? nextValue !== githubTokenState.persistedValue
            : nextValue.trim().length > 0
        : nextValue.trim().length > 0;
    if (!readGitHubTokenValue(fieldIds)) {
        githubTokenState.revealed = false;
    }
    renderGitHubTokenField(fieldIds);
}

async function toggleGitHubTokenVisibility(fieldIds) {
    if (!hasGitHubTokenValue(fieldIds) || githubTokenState.isLoadingReveal) {
        return;
    }
    if (
        githubTokenState.hasPersistedValue
        && !githubTokenState.isDirty
        && !githubTokenState.revealed
        && !githubTokenState.persistedValueLoaded
    ) {
        githubTokenState.isLoadingReveal = true;
        renderGitHubTokenToggle(fieldIds);
        try {
            const result = await revealGitHubToken();
            githubTokenState.persistedValue = typeof result?.token === 'string' ? result.token : '';
            githubTokenState.persistedValueLoaded = Boolean(githubTokenState.persistedValue.trim());
        } catch (e) {
            githubTokenState.isLoadingReveal = false;
            renderGitHubTokenToggle(fieldIds);
            showToast({
                title: t('settings.github.load_failed'),
                message: formatMessage('settings.github.load_failed_detail', { error: e.message }),
                tone: 'danger',
            });
            return;
        }
    }
    githubTokenState.isLoadingReveal = false;
    githubTokenState.revealed = !githubTokenState.revealed;
    renderGitHubTokenField(fieldIds);
}

function readGitHubTokenValue(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
    const inputValue = tokenInput ? tokenInput.value.trim() : '';
    if (!githubTokenState.hasPersistedValue) {
        return inputValue || null;
    }
    if (githubTokenState.isDirty) {
        return inputValue || null;
    }
    return null;
}

function readGitHubWebhookBaseUrl(fieldIds) {
    const webhookBaseUrlInput = document.getElementById(fieldIds.webhookBaseUrlInputId);
    return normalizeGitHubWebhookBaseUrl(webhookBaseUrlInput ? webhookBaseUrlInput.value : '');
}

function normalizeGitHubWebhookBaseUrl(value) {
    return typeof value === 'string' && value.trim()
        ? value.trim()
        : null;
}

function armGitHubTokenInput() {
    githubTokenState.armedForInput = true;
}

function disarmGitHubTokenInput() {
    githubTokenState.armedForInput = false;
}

function canAcceptGitHubTokenInput(tokenInput) {
    if (!tokenInput) {
        return false;
    }
    if (githubTokenState.armedForInput) {
        return true;
    }
    if (typeof document !== 'object' || document === null) {
        return false;
    }
    return document.activeElement === tokenInput;
}

function renderGitHubCallbackPreview(fieldIds) {
    const callbackPreviewEl = document.getElementById(fieldIds.callbackPreviewId);
    if (!callbackPreviewEl) {
        return;
    }

    const callbackUrl = buildGitHubCallbackUrl(readGitHubWebhookBaseUrl(fieldIds));
    callbackPreviewEl.textContent = callbackUrl || t('settings.github.callback_preview_empty');
    callbackPreviewEl.title = callbackUrl || '';
}

function renderGitHubTunnelHelp() {
    const tunnelHelpEl = typeof document.querySelector === 'function'
        ? document.querySelector('.github-webhook-tunnel-note')
        : null;
    if (!tunnelHelpEl) {
        return;
    }
    tunnelHelpEl.title = t('settings.github.tunnel_help');
}

function buildGitHubCallbackUrl(webhookBaseUrl) {
    if (!webhookBaseUrl) {
        return null;
    }

    try {
        const parsed = new URL(webhookBaseUrl);
        if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.host) {
            return null;
        }
        const basePath = parsed.pathname.replace(/\/+$/, '');
        parsed.pathname = `${basePath}/api/triggers/github/deliveries`;
        parsed.search = '';
        parsed.hash = '';
        return parsed.toString();
    } catch (_error) {
        return null;
    }
}

function buildGitHubWebhookTunnelMessage(status) {
    if (!status || status.status === 'idle') {
        return t('settings.github.tunnel_idle');
    }
    if (status.status === 'starting') {
        return status.last_message || t('settings.github.tunnel_starting');
    }
    if (status.status === 'active' && status.public_url) {
        return formatMessage('settings.github.tunnel_active', {
            public_url: status.public_url,
            local_host: status.local_host || '127.0.0.1',
            local_port: status.local_port || 8000,
        });
    }
    if (status.status === 'stopped') {
        return formatMessage('settings.github.tunnel_stopped_message', {
            public_url: status.public_url || '-',
        });
    }
    return formatMessage('settings.github.tunnel_failed', {
        reason: status.error_message || status.last_message || t('settings.github.tunnel_failed_unknown'),
    });
}

function renderGitHubTokenField(fieldIds) {
    const tokenInput = document.getElementById(fieldIds.tokenInputId);
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

    renderGitHubTokenToggle(fieldIds);
}

function renderGitHubTokenToggle(fieldIds) {
    const toggleTokenBtn = document.getElementById(fieldIds.toggleTokenButtonId);
    if (!toggleTokenBtn) {
        return;
    }

    toggleTokenBtn.style.display = hasGitHubTokenValue(fieldIds) ? 'inline-flex' : 'none';
    toggleTokenBtn.disabled = githubTokenState.isLoadingReveal;
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
    if (githubTokenState.hasPersistedValue && !githubTokenState.isDirty) {
        return true;
    }
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
