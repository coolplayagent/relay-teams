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
import { errorToPayload, logError } from '../../utils/logger.js';

let lastProbeState = null;

export function bindProxySettingsHandlers() {
    const saveBtn = document.getElementById('save-proxy-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveProxy;
    }

    const probeBtn = document.getElementById('test-proxy-web-btn');
    if (probeBtn) {
        probeBtn.onclick = handleProbeWeb;
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
            title: 'Load Failed',
            message: `Failed to load proxy config: ${e.message}`,
            tone: 'danger',
        });
    }
}

async function handleSaveProxy() {
    try {
        await saveProxyConfig(readProxyFormValues());
        showToast({
            title: 'Proxy Saved',
            message: 'Proxy settings saved and reloaded.',
            tone: 'success',
        });
        await loadProxyStatusPanel();
    } catch (e) {
        showToast({
            title: 'Save Failed',
            message: `Failed to save proxy config: ${e.message}`,
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
            message: 'Enter a target URL before testing connectivity.',
        };
        renderProxyProbeState();
        return;
    }

    lastProbeState = {
        status: 'probing',
        message: 'Testing connectivity...',
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
            message: `Probe failed: ${e.message}`,
        };
    }

    renderProxyProbeState();
}

function buildProbeState(result) {
    if (result.ok) {
        return {
            status: 'success',
            message: `${result.used_method} ${result.status_code} in ${result.latency_ms}ms`,
        };
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    const statusText = result.status_code ? ` HTTP ${result.status_code}.` : '';
    return {
        status: 'failed',
        message: `${reason}${statusText}`,
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
        probeBtn.textContent = 'Test URL';
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = lastProbeState.message;
    statusEl.className = `proxy-probe-status probe-status probe-status-${lastProbeState.status}`;
    probeBtn.disabled = lastProbeState.status === 'probing';
    probeBtn.textContent = lastProbeState.status === 'probing' ? 'Testing...' : 'Test URL';
}

function writeProxyFormValues(config) {
    setInputValue('proxy-http-proxy', config.http_proxy);
    setInputValue('proxy-https-proxy', config.https_proxy);
    setInputValue('proxy-all-proxy', config.all_proxy);
    setInputValue('proxy-no-proxy', config.no_proxy);
    setInputValue('proxy-username', config.proxy_username);
    setInputValue('proxy-password', config.proxy_password);
    setInputValue('proxy-ssl-verify', serializeTriStateValue(config.ssl_verify));
}

function readProxyFormValues() {
    return {
        http_proxy: readInputValue('proxy-http-proxy'),
        https_proxy: readInputValue('proxy-https-proxy'),
        all_proxy: readInputValue('proxy-all-proxy'),
        no_proxy: readInputValue('proxy-no-proxy'),
        proxy_username: readInputValue('proxy-username'),
        proxy_password: readInputValue('proxy-password'),
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

function serializeTriStateValue(value) {
    if (value === true) {
        return 'true';
    }
    if (value === false) {
        return 'false';
    }
    return '';
}
