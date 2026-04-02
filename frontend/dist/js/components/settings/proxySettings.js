/**
 * components/settings/proxySettings.js
 * Proxy form persistence and connectivity checks.
 */
import {
    fetchProxyConfig,
    probeWebConnectivity,
    saveProxyConfig,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const MASKED_SECRET_PLACEHOLDER = '************';

let lastProbeState = null;
let languageBound = false;
let proxyPasswordState = createProxyPasswordState();

export function bindProxySettingsHandlers() {
    const saveBtn = document.getElementById('save-proxy-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveProxy;
    }

    const probeBtn = document.getElementById('test-proxy-web-btn');
    if (probeBtn) {
        probeBtn.onclick = handleProbeWeb;
    }

    const passwordInput = document.getElementById('proxy-password');
    if (passwordInput) {
        passwordInput.oninput = handleProxyPasswordInput;
        passwordInput.onchange = handleProxyPasswordInput;
    }

    const togglePasswordBtn = document.getElementById('toggle-proxy-password-btn');
    if (togglePasswordBtn) {
        togglePasswordBtn.onclick = toggleProxyPasswordVisibility;
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderProxyPasswordField();
            renderProxyProbeState();
        });
        languageBound = true;
    }
}

export async function loadProxyStatusPanel() {
    try {
        const config = await fetchProxyConfig();
        writeProxyFormValues(config);
        renderProxyProbeState();
    } catch (e) {
        logError(
            'frontend.proxy_settings.load_failed',
            'Failed to load proxy config',
            errorToPayload(e),
        );
        showToast({
            title: t('settings.proxy.load_failed'),
            message: formatMessage('settings.proxy.load_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleSaveProxy() {
    try {
        await saveProxyConfig(readProxyFormValues());
        showToast({
            title: t('settings.proxy.saved'),
            message: t('settings.proxy.saved_message'),
            tone: 'success',
        });
        await loadProxyStatusPanel();
    } catch (e) {
        showToast({
            title: t('settings.proxy.save_failed'),
            message: formatMessage('settings.proxy.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleProbeWeb() {
    const urlInput = document.getElementById('proxy-probe-url');
    const timeoutInput = document.getElementById('proxy-probe-timeout');
    if (!urlInput || !timeoutInput) {
        return;
    }

    const url = urlInput.value.trim();
    const timeoutMs = parseInt(timeoutInput.value, 10) || 5000;
    if (!url) {
        lastProbeState = {
            status: 'failed',
            message: t('settings.proxy.enter_url'),
        };
        renderProxyProbeState();
        return;
    }

    lastProbeState = {
        status: 'probing',
        message: t('settings.proxy.testing_message'),
    };
    renderProxyProbeState();

    try {
        const result = await probeWebConnectivity({
            url,
            timeout_ms: timeoutMs,
            proxy_override: readProxyFormValues(),
        });
        lastProbeState = buildProbeState(result);
    } catch (e) {
        lastProbeState = {
            status: 'failed',
            message: formatMessage('settings.proxy.probe_failed', { error: e.message }),
        };
    }

    renderProxyProbeState();
}

function buildProbeState(result) {
    if (result.ok) {
        return {
            status: 'success',
            message: formatMessage('settings.proxy.probe_success', {
                method: result.used_method,
                status_code: result.status_code,
                latency_ms: result.latency_ms,
            }),
        };
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    const statusText = result.status_code ? ` HTTP ${result.status_code}.` : '';
    return {
        status: 'failed',
        message: formatMessage('settings.proxy.probe_reason', { reason, status_text: statusText }),
    };
}

function renderProxyProbeState() {
    const statusEl = document.getElementById('proxy-probe-status');
    const probeBtn = document.getElementById('test-proxy-web-btn');
    if (!statusEl || !probeBtn) {
        return;
    }

    if (!lastProbeState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'proxy-probe-status';
        probeBtn.disabled = false;
        probeBtn.textContent = t('settings.proxy.test_url');
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = lastProbeState.message;
    statusEl.className = `proxy-probe-status probe-status probe-status-${lastProbeState.status}`;
    probeBtn.disabled = lastProbeState.status === 'probing';
    probeBtn.textContent = lastProbeState.status === 'probing'
        ? t('settings.proxy.testing')
        : t('settings.proxy.test_url');
}

function writeProxyFormValues(config) {
    setInputValue('proxy-http-proxy', config.http_proxy);
    setInputValue('proxy-https-proxy', config.https_proxy);
    setInputValue('proxy-all-proxy', config.all_proxy);
    setInputValue('proxy-no-proxy', config.no_proxy);
    setInputValue('proxy-username', config.proxy_username);
    proxyPasswordState = createProxyPasswordState(config.proxy_password);
    renderProxyPasswordField();
    setInputValue('proxy-ssl-verify', serializeProxySslVerifyValue(config.ssl_verify));
}

function readProxyFormValues() {
    return {
        http_proxy: readInputValue('proxy-http-proxy'),
        https_proxy: readInputValue('proxy-https-proxy'),
        all_proxy: readInputValue('proxy-all-proxy'),
        no_proxy: readInputValue('proxy-no-proxy'),
        proxy_username: readInputValue('proxy-username'),
        proxy_password: readProxyPasswordValue(),
        ssl_verify: parseTriStateValue(readInputValue('proxy-ssl-verify')),
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

function parseTriStateValue(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'true') {
        return true;
    }
    if (normalized === 'false') {
        return false;
    }
    return null;
}

function serializeProxySslVerifyValue(value) {
    if (value === null || value === undefined) {
        return 'false';
    }
    return serializeTriStateValue(value);
}

function serializeTriStateValue(value) {
    if (value === true) {
        return 'true';
    }
    if (value === false) {
        return 'false';
    }
    return '';
}

function createProxyPasswordState(persistedValue = null) {
    const normalizedValue = typeof persistedValue === 'string' ? persistedValue : '';
    return {
        persistedValue: normalizedValue,
        draftValue: '',
        hasPersistedValue: Boolean(normalizedValue.trim()),
        isDirty: false,
        revealed: false,
    };
}

function handleProxyPasswordInput() {
    const passwordInput = document.getElementById('proxy-password');
    const nextValue = passwordInput ? passwordInput.value : '';
    proxyPasswordState.draftValue = nextValue;
    proxyPasswordState.isDirty = proxyPasswordState.hasPersistedValue
        ? nextValue !== proxyPasswordState.persistedValue
        : nextValue.trim().length > 0;
    if (!readProxyPasswordValue()) {
        proxyPasswordState.revealed = false;
    }
    renderProxyPasswordField();
}

function toggleProxyPasswordVisibility() {
    if (!hasProxyPasswordValue()) {
        return;
    }
    proxyPasswordState.revealed = !proxyPasswordState.revealed;
    renderProxyPasswordField();
}

function readProxyPasswordValue() {
    const passwordInput = document.getElementById('proxy-password');
    const inputValue = passwordInput ? passwordInput.value.trim() : '';
    if (!proxyPasswordState.hasPersistedValue) {
        return inputValue || null;
    }
    if (proxyPasswordState.isDirty) {
        return inputValue || null;
    }
    return inputValue || proxyPasswordState.persistedValue || null;
}

function renderProxyPasswordField() {
    const passwordInput = document.getElementById('proxy-password');
    if (!passwordInput) {
        return;
    }

    if (proxyPasswordState.revealed) {
        passwordInput.type = 'text';
        passwordInput.value = proxyPasswordState.isDirty
            ? proxyPasswordState.draftValue
            : proxyPasswordState.persistedValue;
        passwordInput.placeholder = '';
    } else if (proxyPasswordState.hasPersistedValue && !proxyPasswordState.isDirty) {
        passwordInput.type = 'password';
        passwordInput.value = '';
        passwordInput.placeholder = MASKED_SECRET_PLACEHOLDER;
    } else {
        passwordInput.type = 'password';
        passwordInput.value = proxyPasswordState.draftValue;
        passwordInput.placeholder = t('settings.proxy.password_placeholder');
    }

    renderProxyPasswordToggle();
}

function renderProxyPasswordToggle() {
    const togglePasswordBtn = document.getElementById('toggle-proxy-password-btn');
    if (!togglePasswordBtn) {
        return;
    }

    togglePasswordBtn.style.display = hasProxyPasswordValue() ? 'inline-flex' : 'none';
    togglePasswordBtn.className = proxyPasswordState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    togglePasswordBtn.title = proxyPasswordState.revealed
        ? t('settings.proxy.hide_password')
        : t('settings.proxy.show_password');
    if (typeof togglePasswordBtn.setAttribute === 'function') {
        togglePasswordBtn.setAttribute('aria-label', togglePasswordBtn.title);
    } else {
        togglePasswordBtn.ariaLabel = togglePasswordBtn.title;
    }
}

function hasProxyPasswordValue() {
    const passwordInput = document.getElementById('proxy-password');
    const inputValue = passwordInput ? passwordInput.value.trim() : '';
    if (proxyPasswordState.hasPersistedValue && !proxyPasswordState.isDirty) {
        return Boolean(proxyPasswordState.persistedValue || inputValue);
    }
    return Boolean(proxyPasswordState.draftValue.trim() || inputValue);
}
