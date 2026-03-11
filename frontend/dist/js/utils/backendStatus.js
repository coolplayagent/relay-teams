/**
 * utils/backendStatus.js
 * Tracks backend availability and reflects it in the sidebar status indicator.
 */
import { els } from './dom.js';

const HEALTH_POLL_MS = 15000;
const HEALTH_TIMEOUT_MS = 4000;

let healthPollTimer = null;
let inFlightHealthCheck = null;
let backendStatus = 'checking';

export function initBackendStatusMonitor() {
    applyBackendStatus('checking', 'Checking backend...');
    void refreshBackendStatus({ force: true });
    if (healthPollTimer) return;
    healthPollTimer = window.setInterval(() => {
        void refreshBackendStatus();
    }, HEALTH_POLL_MS);
}

export function markBackendOnline(label = 'Backend Connected') {
    applyBackendStatus('online', label);
}

export function markBackendOffline(label = 'Backend Offline') {
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
            markBackendOffline('Backend Unavailable');
            return false;
        }
        const payload = await response.json();
        const safeStatus = String(payload?.status || '').trim().toLowerCase();
        if (safeStatus === 'ok') {
            markBackendOnline('Backend Connected');
            return true;
        }
        markBackendOffline('Backend Unavailable');
        return false;
    } catch (_) {
        markBackendOffline('Backend Offline');
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
    if (status === 'online') return 'Backend Connected';
    if (status === 'offline') return 'Backend Offline';
    return 'Checking backend...';
}

export function getBackendStatus() {
    return backendStatus;
}
