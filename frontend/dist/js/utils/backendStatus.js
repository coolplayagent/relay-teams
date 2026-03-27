/**
 * utils/backendStatus.js
 * Tracks backend availability and reflects it in the sidebar status indicator.
 */
import { els } from './dom.js';
import { t } from './i18n.js';

const HEALTH_POLL_MS = 15000;
const HEALTH_TIMEOUT_MS = 4000;

let healthPollTimer = null;
let inFlightHealthCheck = null;
let backendStatus = 'checking';

export function initBackendStatusMonitor() {
    applyBackendStatus('checking', t('backend.status.checking'));
    void refreshBackendStatus({ force: true });
    if (healthPollTimer) return;
    healthPollTimer = window.setInterval(() => {
        void refreshBackendStatus();
    }, HEALTH_POLL_MS);
}

export function markBackendOnline(label = t('backend.status.connected')) {
    applyBackendStatus('online', label);
}

export function markBackendOffline(label = t('backend.status.offline')) {
    applyBackendStatus('offline', label);
}

export async function refreshBackendStatus({ force = false } = {}) {
    if (inFlightHealthCheck && !force) {
        return inFlightHealthCheck;
    }
    inFlightHealthCheck = probeBackendHealth()
        .finally(() => {
            inFlightHealthCheck = null;
        });
    return inFlightHealthCheck;
}

async function probeBackendHealth() {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
    try {
        const response = await fetch('/api/system/health', {
            method: 'GET',
            cache: 'no-store',
            headers: {
                Accept: 'application/json',
            },
            signal: controller.signal,
        });
        if (!response.ok) {
            markBackendOffline(t('backend.status.unavailable'));
            return false;
        }
        const payload = await response.json();
        const safeStatus = String(payload?.status || '').trim().toLowerCase();
        if (safeStatus === 'ok') {
            markBackendOnline(t('backend.status.connected'));
            return true;
        }
        markBackendOffline(t('backend.status.unavailable'));
        return false;
    } catch (_) {
        markBackendOffline(t('backend.status.offline'));
        return false;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

function applyBackendStatus(nextStatus, label) {
    backendStatus = nextStatus;
    if (!els.backendStatus) return;
    els.backendStatus.classList.remove('online', 'offline', 'checking');
    els.backendStatus.classList.add(nextStatus);
    els.backendStatus.dataset.status = nextStatus;
    const safeLabel = String(label || defaultLabelForStatus(nextStatus));
    if (els.backendStatusLabel) {
        els.backendStatusLabel.textContent = safeLabel;
    } else {
        els.backendStatus.textContent = safeLabel;
    }
    els.backendStatus.title = safeLabel;
}

function defaultLabelForStatus(status) {
    if (status === 'online') return t('backend.status.connected');
    if (status === 'offline') return t('backend.status.offline');
    return t('backend.status.checking');
}

export function getBackendStatus() {
    return backendStatus;
}
