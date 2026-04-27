/**
 * utils/backendStatus.js
 * Tracks backend availability and reflects it in the sidebar status indicator.
 */
import { els } from './dom.js';
import { t } from './i18n.js';

const HEALTH_POLL_MS = 15000;
const DISCOVERY_TIMEOUT_MS = 1500;
const CONTROL_PLANE_TIMEOUT_MS = 1500;
const MAIN_LIVE_TIMEOUT_MS = 1500;
const CONTROL_PLANE_FALLBACK_PORT_RANGE = 50;
const CONTROL_PLANE_CACHE_KEY = 'relayTeams.controlPlaneLiveUrl';

let healthPollTimer = null;
let inFlightHealthCheck = null;
let backendStatus = 'checking';
let controlPlaneLiveUrl = readCachedControlPlaneLiveUrl();
let controlPlaneDiscoveryAttempted = false;

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

export function markBackendBusy(label = t('backend.status.busy')) {
    applyBackendStatus('busy', label);
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
    const controlUrl = await resolveControlPlaneLiveUrl();
    if (controlUrl) {
        const controlProbe = await probeJson(controlUrl, CONTROL_PLANE_TIMEOUT_MS);
        if (controlProbe.ok && isControlPlaneLivePayload(controlProbe.payload)) {
            rememberControlPlaneLiveUrl(controlUrl);
            if (await confirmMainBackendOnline()) {
                return true;
            }
            markBackendBusy(t('backend.status.busy'));
            return true;
        }
        forgetControlPlaneLiveUrl(controlUrl);
    }

    if (await confirmMainBackendOnline()) {
        return true;
    }

    const fallbackProbe = await probeFallbackControlPlaneUrls(controlUrl);
    if (fallbackProbe) {
        rememberControlPlaneLiveUrl(fallbackProbe.liveUrl);
        if (await confirmMainBackendOnline()) {
            return true;
        }
        markBackendBusy(t('backend.status.busy'));
        return true;
    }

    markBackendOffline(t('backend.status.offline'));
    return false;
}

async function confirmMainBackendOnline() {
    const mainProbe = await probeJson('/api/system/live', MAIN_LIVE_TIMEOUT_MS);
    if (mainProbe.ok && isLivePayload(mainProbe.payload)) {
        markBackendOnline(t('backend.status.connected'));
        return true;
    }
    return false;
}

async function resolveControlPlaneLiveUrl() {
    if (controlPlaneLiveUrl) {
        return controlPlaneLiveUrl;
    }
    const discoveredUrl = await discoverControlPlaneLiveUrl();
    if (discoveredUrl) {
        rememberControlPlaneLiveUrl(discoveredUrl);
        return discoveredUrl;
    }
    return null;
}

async function discoverControlPlaneLiveUrl() {
    if (controlPlaneDiscoveryAttempted) {
        return null;
    }
    controlPlaneDiscoveryAttempted = true;
    const probe = await probeJson('/api/system/control-plane', DISCOVERY_TIMEOUT_MS);
    if (!probe.ok) {
        controlPlaneDiscoveryAttempted = false;
        return null;
    }
    const payload = probe.payload || {};
    if (payload.enabled !== true) {
        return null;
    }
    return normalizeControlPlaneLiveUrl(payload.live_url);
}

async function probeJson(url, timeoutMs) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, {
            method: 'GET',
            cache: 'no-store',
            headers: {
                Accept: 'application/json',
            },
            signal: controller.signal,
        });
        if (!response.ok) return { ok: false, payload: null };
        return { ok: true, payload: await response.json() };
    } catch (_) {
        return { ok: false, payload: null };
    } finally {
        window.clearTimeout(timeoutId);
    }
}

async function probeFallbackControlPlaneUrls(controlUrl) {
    const fallbackUrls = inferControlPlaneLiveUrls()
        .filter(fallbackUrl => fallbackUrl !== controlUrl);
    const probes = await Promise.all(fallbackUrls.map(async liveUrl => ({
        liveUrl,
        result: await probeJson(liveUrl, CONTROL_PLANE_TIMEOUT_MS),
    })));
    return probes.find(({ result }) => (
        result.ok && isControlPlaneLivePayload(result.payload)
    )) || null;
}

function applyBackendStatus(nextStatus, label) {
    backendStatus = nextStatus;
    if (!els.backendStatus) return;
    els.backendStatus.classList.remove('online', 'offline', 'checking', 'busy');
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
    if (status === 'busy') return t('backend.status.busy');
    return t('backend.status.checking');
}

export function getBackendStatus() {
    return backendStatus;
}

function isLivePayload(payload) {
    const safeStatus = String(payload?.status || '').trim().toLowerCase();
    return safeStatus === 'alive' || safeStatus === 'ok';
}

function isControlPlaneLivePayload(payload) {
    if (!isLivePayload(payload)) {
        return false;
    }
    const mainBaseUrl = String(payload?.main_base_url || '').trim();
    return Boolean(mainBaseUrl)
        && (baseUrlMatchesCurrentOrigin(mainBaseUrl)
            || isInternalBaseUrl(mainBaseUrl));
}

function normalizeControlPlaneLiveUrl(rawUrl) {
    const safeUrl = String(rawUrl || '').trim();
    if (!safeUrl) {
        return null;
    }
    try {
        const url = new URL(safeUrl, window.location.origin);
        if (url.protocol !== 'http:' && url.protocol !== 'https:') {
            return null;
        }
        if (shouldUseCurrentHostForControlPlane(url.hostname)) {
            url.hostname = window.location.hostname;
        }
        return url.href;
    } catch (_) {
        return null;
    }
}

function inferControlPlaneLiveUrls() {
    try {
        const currentOrigin = window.location.origin;
        const url = new URL(currentOrigin);
        const currentPort = Number(effectivePort(url));
        if (!Number.isInteger(currentPort) || currentPort < 1 || currentPort > 65535) {
            return [];
        }
        const urls = [];
        const maxPort = Math.min(65535, currentPort + CONTROL_PLANE_FALLBACK_PORT_RANGE);
        for (let port = currentPort + 1; port <= maxPort; port += 1) {
            urls.push(buildControlPlaneLiveUrl(currentOrigin, port));
        }
        const minPort = Math.max(1, currentPort - CONTROL_PLANE_FALLBACK_PORT_RANGE);
        for (let port = currentPort - 1; port >= minPort; port -= 1) {
            urls.push(buildControlPlaneLiveUrl(currentOrigin, port));
        }
        return urls;
    } catch (_) {
        return [];
    }
}

function buildControlPlaneLiveUrl(origin, port) {
    const url = new URL(origin);
    url.port = String(port);
    url.pathname = '/live';
    url.search = '';
    url.hash = '';
    return url.href;
}

function shouldUseCurrentHostForControlPlane(hostname) {
    if (isWildcardHost(hostname)) {
        return true;
    }
    return isLoopbackHost(hostname) && !isLoopbackHost(window.location.hostname);
}

function baseUrlMatchesCurrentOrigin(rawUrl) {
    try {
        const expected = new URL(rawUrl);
        const current = new URL(window.location.origin);
        const expectedHost = isWildcardHost(expected.hostname)
            ? normalizeComparableHost(current.hostname)
            : normalizeComparableHost(expected.hostname);
        return expected.protocol === current.protocol
            && expectedHost === normalizeComparableHost(current.hostname)
            && effectivePort(expected) === effectivePort(current);
    } catch (_) {
        return true;
    }
}

function isInternalBaseUrl(rawUrl) {
    try {
        const url = new URL(rawUrl);
        return isWildcardHost(url.hostname)
            || isLoopbackHost(url.hostname)
            || isPrivateNetworkHost(url.hostname);
    } catch (_) {
        return false;
    }
}

function isWildcardHost(hostname) {
    const normalized = String(hostname || '').toLowerCase();
    return normalized === '0.0.0.0' || normalized === '::' || normalized === '[::]';
}

function normalizeComparableHost(hostname) {
    return isLoopbackHost(hostname) ? 'loopback' : String(hostname || '').toLowerCase();
}

function isLoopbackHost(hostname) {
    const normalized = String(hostname || '').toLowerCase();
    return normalized === 'localhost'
        || normalized === '127.0.0.1'
        || normalized === '::1'
        || normalized === '[::1]';
}

function isPrivateNetworkHost(hostname) {
    const normalized = String(hostname || '').toLowerCase().replace(/^\[|\]$/g, '');
    if (normalized.startsWith('10.') || normalized.startsWith('192.168.')) {
        return true;
    }
    const match = normalized.match(/^172\.(\d{1,2})\./);
    if (match) {
        const secondOctet = Number(match[1]);
        return secondOctet >= 16 && secondOctet <= 31;
    }
    return normalized.startsWith('fc') || normalized.startsWith('fd');
}

function effectivePort(url) {
    if (url.port) return url.port;
    return url.protocol === 'https:' ? '443' : '80';
}

function readCachedControlPlaneLiveUrl() {
    try {
        return normalizeControlPlaneLiveUrl(window.localStorage.getItem(CONTROL_PLANE_CACHE_KEY));
    } catch (_) {
        return null;
    }
}

function rememberControlPlaneLiveUrl(liveUrl) {
    controlPlaneLiveUrl = liveUrl;
    try {
        window.localStorage.setItem(CONTROL_PLANE_CACHE_KEY, liveUrl);
    } catch (_) {
        // Storage may be unavailable in private contexts; probing still works in memory.
    }
}

function forgetControlPlaneLiveUrl(liveUrl) {
    if (controlPlaneLiveUrl === liveUrl) {
        controlPlaneLiveUrl = null;
    }
    controlPlaneDiscoveryAttempted = false;
    try {
        if (window.localStorage.getItem(CONTROL_PLANE_CACHE_KEY) === liveUrl) {
            window.localStorage.removeItem(CONTROL_PLANE_CACHE_KEY);
        }
    } catch (_) {
        // Storage may be unavailable in private contexts; the next poll will rediscover.
    }
}
