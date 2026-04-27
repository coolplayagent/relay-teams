/**
 * utils/logger.js
 * UI log rendering plus frontend log shipping.
 */
import { state } from '../core/state.js';
import { els } from './dom.js';

const FRONTEND_LOG_ENDPOINT = '/api/logs/frontend';
const FLUSH_INTERVAL_MS = 1000;
const MAX_BATCH_SIZE = 20;
const MAX_PENDING_EVENTS = 200;
const MAX_PENDING_WHEN_BACKED_UP = 80;
const MAX_MESSAGE_LENGTH = 2000;

const browserSessionId = `browser_${Math.random().toString(36).slice(2, 10)}`;

let pendingEvents = [];
let flushTimer = null;
let installedGlobalHandlers = false;

function nowIso() {
    return new Date().toISOString();
}

function truncateMessage(message) {
    const text = String(message || '');
    if (text.length <= MAX_MESSAGE_LENGTH) {
        return text;
    }
    return `${text.slice(0, MAX_MESSAGE_LENGTH)}...(truncated)`;
}

function getPagePath() {
    return globalThis.location?.pathname || '/';
}

function buildBaseEvent(level, event, message, payload = {}) {
    const traceId = state.activeRunId ? String(state.activeRunId) : null;
    return {
        level,
        event,
        message: truncateMessage(message),
        trace_id: traceId,
        request_id: null,
        run_id: traceId,
        session_id: String(state.currentSessionId || '') || null,
        task_id: null,
        instance_id: null,
        role_id: null,
        page: globalThis.document?.title || 'agent-teams',
        route: getPagePath(),
        browser_session_id: browserSessionId,
        user_agent: globalThis.navigator?.userAgent || 'unknown',
        payload,
        ts: nowIso(),
    };
}

function scheduleFlush() {
    if (flushTimer !== null) {
        return;
    }
    flushTimer = globalThis.setTimeout(() => {
        flushTimer = null;
        void flushFrontendLogs();
    }, FLUSH_INTERVAL_MS);
}

function enqueueEvent(event) {
    if (
        pendingEvents.length >= MAX_PENDING_WHEN_BACKED_UP
        && (event.level === 'debug' || event.level === 'info')
    ) {
        return;
    }
    pendingEvents.push(event);
    if (pendingEvents.length > MAX_PENDING_EVENTS) {
        pendingEvents = pendingEvents.slice(-MAX_PENDING_EVENTS);
    }
    if (pendingEvents.length >= MAX_BATCH_SIZE) {
        void flushFrontendLogs();
        return;
    }
    scheduleFlush();
}

function buildPayload(error, extra = {}) {
    const payload = { ...extra };
    if (error instanceof Error) {
        payload.error_name = error.name;
        payload.error_message = truncateMessage(error.message);
        if (error.stack) {
            payload.error_stack = truncateMessage(error.stack);
        }
        return payload;
    }
    if (typeof error === 'string') {
        payload.error_message = truncateMessage(error);
        return payload;
    }
    if (error !== undefined && error !== null) {
        payload.error_value = truncateMessage(JSON.stringify(error));
    }
    return payload;
}

async function postLogBatch(events, useKeepalive = false) {
    if (!events.length) {
        return;
    }
    const body = JSON.stringify({ events });
    if (
        useKeepalive
        && typeof navigator !== 'undefined'
        && typeof navigator.sendBeacon === 'function'
    ) {
        const beaconOk = navigator.sendBeacon(
            FRONTEND_LOG_ENDPOINT,
            new Blob([body], { type: 'application/json' }),
        );
        if (beaconOk) {
            return;
        }
    }
    await fetch(FRONTEND_LOG_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: useKeepalive,
    });
}

export async function flushFrontendLogs({ useKeepalive = false } = {}) {
    if (!pendingEvents.length) {
        return;
    }
    if (flushTimer !== null) {
        globalThis.clearTimeout(flushTimer);
        flushTimer = null;
    }
    const events = pendingEvents.slice(0, MAX_BATCH_SIZE);
    pendingEvents = pendingEvents.slice(events.length);
    try {
        await postLogBatch(events, useKeepalive);
    } catch (_) {
        pendingEvents = events.concat(pendingEvents).slice(-MAX_PENDING_EVENTS);
    }
}

export function sysLog(msg, type = 'info') {
    if (!els.systemLogs) return;

    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;

    const time = new Date().toLocaleTimeString();
    entry.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-msg">${msg}</span>`;

    els.systemLogs.appendChild(entry);
    els.systemLogs.scrollTop = els.systemLogs.scrollHeight;
}

export function logDebug(event, message, payload = {}) {
    enqueueEvent(buildBaseEvent('debug', event, message, payload));
}

export function logInfo(event, message, payload = {}) {
    enqueueEvent(buildBaseEvent('info', event, message, payload));
}

export function logWarn(event, message, payload = {}) {
    enqueueEvent(buildBaseEvent('warn', event, message, payload));
}

export function logError(event, message, payload = {}) {
    enqueueEvent(buildBaseEvent('error', event, message, payload));
}

export function errorToPayload(error, extra = {}) {
    return buildPayload(error, extra);
}

export function installGlobalErrorLogging() {
    if (installedGlobalHandlers) {
        return;
    }
    installedGlobalHandlers = true;

    globalThis.addEventListener('error', event => {
        logError(
            'window.error',
            event.message || 'Unhandled window error',
            buildPayload(event.error, {
                filename: event.filename || '',
                lineno: event.lineno || 0,
                colno: event.colno || 0,
            }),
        );
    });

    globalThis.addEventListener('unhandledrejection', event => {
        logError(
            'window.unhandledrejection',
            'Unhandled promise rejection',
            buildPayload(event.reason),
        );
    });

    globalThis.addEventListener('beforeunload', () => {
        void flushFrontendLogs({ useKeepalive: true });
    });
}
